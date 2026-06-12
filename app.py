"""
Webhook BotConversa -> comentário no card do Agendor.
Serviço Flask para deploy no Railway (sem banco; só recebe e repassa).
O consultor confirma os dados DENTRO do BotConversa; o webhook só dispara depois disso.
"""

import os
import time
import requests
from flask import Flask, request, jsonify
import buscar as busca

app = Flask(__name__)

AGENDOR_BASE = "https://api.agendor.com.br/v3"
AGENDOR_TOKEN = os.environ.get("AGENDOR_TOKEN")      # token de admin da API do Agendor
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")     # segredo combinado com o BotConversa


def montar_comentario(d):
    """Monta o texto do comentário a partir dos campos confirmados pela IA."""
    linhas = [
        "Registro automático via SDR IA (confirmado pelo consultor).",
        f"Consultor: {d['consultor']}" if d.get("consultor") else None,
        f"Decisor: {d['decisor']}" if d.get("decisor") else None,
        f"Unidades do condomínio: {d['unidades']}" if d.get("unidades") else None,
        f"Orçamento: {d['orcamento']}" if d.get("orcamento") else None,
        f"Próximo passo: {d['proximoPasso']}" if d.get("proximoPasso") else None,
        f"Resumo: {d['resumo']}" if d.get("resumo") else None,
    ]
    return "\n".join([l for l in linhas if l])


def post_agendor(path, body, tentativa=0):
    """POST no Agendor com 1 retry caso bata no limite de 4 req/s (429)."""
    resp = requests.post(
        f"{AGENDOR_BASE}{path}",
        headers={
            "Authorization": f"Token {AGENDOR_TOKEN}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=15,
    )
    if resp.status_code == 429 and tentativa < 2:
        time.sleep(0.5 * (tentativa + 1))
        return post_agendor(path, body, tentativa + 1)
    return resp


def buscar_deal_por_telefone(telefone):
    """Fallback: acha o negócio pelo telefone se o dealId não vier no payload."""
    resp = requests.get(
        f"{AGENDOR_BASE}/deals",
        headers={"Authorization": f"Token {AGENDOR_TOKEN}"},
        params={"contact": telefone},
        timeout=15,
    )
    if not resp.ok:
        return None
    data = resp.json().get("data") or []
    return data[0]["id"] if data else None


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})


@app.route("/webhook/agendor", methods=["POST"])
def webhook_agendor():
    # Trava de segurança: só aceita chamadas com o segredo combinado.
    if WEBHOOK_SECRET and request.headers.get("x-webhook-secret") != WEBHOOK_SECRET:
        return jsonify({"erro": "Segredo inválido"}), 401

    d = request.get_json(silent=True) or {}

    # Card: prioriza o dealId vindo do campo do contato; cai pro telefone se faltar.
    deal_id = d.get("dealId")
    if not deal_id and d.get("telefone"):
        deal_id = buscar_deal_por_telefone(d["telefone"])
    if not deal_id:
        return jsonify({"erro": "Negócio não encontrado (envie dealId ou telefone)"}), 404

    corpo = {
        "text": montar_comentario(d),
        "type": "OTHER",     # ajuste se quiser LIGACAO, EMAIL, REUNIAO...
        "finished": True,    # registra como já realizada (vira comentário no histórico)
    }

    r = post_agendor(f"/deals/{deal_id}/tasks", corpo)

    if r.status_code == 401:
        return jsonify({"erro": "Token Agendor inválido"}), 401
    if r.status_code == 404:
        return jsonify({"erro": f"Deal {deal_id} não existe"}), 404
    if not r.ok:
        return jsonify({"erro": "Falha no Agendor", "status": r.status_code, "detalhe": r.text}), 502

    criado = r.json()
    return jsonify({
        "ok": True,
        "dealId": deal_id,
        "atividade": (criado.get("data") or {}).get("id"),
    }), 200


@app.route("/buscar", methods=["POST"])
def buscar_lead():
    d = request.get_json(silent=True) or {}
    if WEBHOOK_SECRET and request.headers.get("x-webhook-secret") != WEBHOOK_SECRET:
        return jsonify({"erro": "Segredo inválido"}), 401
    telefone = d.get("telefone")
    termo = d.get("termo", "")
    if not telefone:
        return jsonify({"erro": "Envie 'telefone' do consultor"}), 400
    resultado, codigo = busca.buscar(
        telefone, termo,
        limite=int(d.get("limite", 5)),
        recente=bool(d.get("recente", False)),
    )
    return jsonify(resultado), codigo


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
