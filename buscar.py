"""
Busca de leads na carteira do consultor (ao vivo no Agendor + fuzzy do nosso lado).
O consultor fala um nome solto; aqui a gente acha os candidatos só entre os negócios
sob responsabilidade dele e devolve ranqueado com dados de desempate.
"""

import os
import re
import time
import unicodedata
import requests
from rapidfuzz import fuzz

AGENDOR_BASE = "https://api.agendor.com.br/v3"
AGENDOR_TOKEN = os.environ.get("AGENDOR_TOKEN")

# De-para: WhatsApp do consultor -> usuário dele no Agendor.
# A âncora é o TELEFONE (fixo). O nome do responsável NÃO fica aqui — vem do
# próprio Agendor junto dos candidatos, então sempre reflete quem está na carteira hoje.
# A chave é só dígitos. O userId você pega na rota GET /users do Agendor.
CONSULTORES = {
    "5511943800383": {"userId": 70},
    # "55XXXXXXXXXXX": {"userId": 0},
}

# Cache da base de negócios (admin vê tudo; filtramos por consultor em memória).
_CACHE = {"deals": None, "at": 0}
_CACHE_TTL = 600  # 10 min

# Cortes de confiança do fuzzy (0-100).
CORTE_LISTAR = 70       # >= aqui: match confiável, lista pra confirmar
PISO_APROXIMADO = 55    # entre o piso e o corte: oferece só o mais aproximado
                        # abaixo do piso: "condomínio não localizado"

# Prefixos que não ajudam a casar nome de condomínio.
_PREFIXOS = r"\b(condominio|condominial|cond|edificio|edif|ed|residencial|resid|res|bloco|torre)\b"


def so_digitos(s):
    return re.sub(r"\D", "", s or "")


def normalizar(texto):
    """Minúsculo, sem acento, sem pontuação, sem prefixos de condomínio."""
    if not texto:
        return ""
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    t = t.lower()
    t = re.sub(_PREFIXOS, " ", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# Palavras de ligação que não contam como "nome citado".
_LIGACAO = {"das", "dos", "de", "da", "do", "e"}


def _pontuar(termo_n, alvo_n):
    """
    Procura cada palavra que o consultor falou dentro do nome cadastrado, com
    margem pra grafia (Vilas≈Villas). Não compara tamanho: palavra que sobra no
    cadastro ('Residencial') não penaliza. A nota é o quão bem as palavras ditas
    foram encontradas no nome.
    """
    termo_tokens = [w for w in termo_n.split() if len(w) >= 2 and w not in _LIGACAO]
    alvo_tokens = [w for w in alvo_n.split() if w not in _LIGACAO]
    if not termo_tokens or not alvo_tokens:
        return 0.0
    # melhor correspondência (com tolerância a grafia) de cada palavra dita
    por_palavra = [max(fuzz.ratio(t, a) for a in alvo_tokens) for t in termo_tokens]
    return sum(por_palavra) / len(por_palavra)


def _agendor_get(path, params=None, tentativa=0):
    resp = requests.get(
        f"{AGENDOR_BASE}{path}",
        headers={"Authorization": f"Token {AGENDOR_TOKEN}"},
        params=params or {},
        timeout=20,
    )
    if resp.status_code == 429 and tentativa < 3:
        time.sleep(0.5 * (tentativa + 1))
        return _agendor_get(path, params, tentativa + 1)
    return resp


def carregar_base():
    """Puxa todos os negócios (paginado) e guarda em cache por alguns minutos."""
    agora = time.time()
    if _CACHE["deals"] is not None and (agora - _CACHE["at"]) < _CACHE_TTL:
        return _CACHE["deals"]

    deals, page = [], 1
    while page <= 60:  # teto de segurança (~6000 negócios)
        r = _agendor_get("/deals", {"page": page, "per_page": 100})
        if not r.ok:
            break
        lote = r.json().get("data") or []
        if not lote:
            break
        deals.extend(lote)
        if len(lote) < 100:
            break
        page += 1

    _CACHE["deals"] = deals
    _CACHE["at"] = agora
    return deals


def _id_do_responsavel(deal):
    for chave in ("userOwner", "user"):
        u = deal.get(chave) or {}
        if u.get("userId"):
            yield u["userId"]


def carteira_do_consultor(user_id):
    return [d for d in carregar_base() if user_id in set(_id_do_responsavel(d))]


def _nome_responsavel(carteira, user_id):
    """Pega o nome do responsável no próprio Agendor (não no de-para)."""
    for d in carteira:
        for chave in ("userOwner", "user"):
            u = d.get(chave) or {}
            if u.get("userId") == user_id and u.get("name"):
                return u["name"]
    return None


def _campos_busca(deal):
    org = (deal.get("organization") or {}).get("name") or ""
    pes = (deal.get("person") or {}).get("name") or ""
    tit = deal.get("title") or ""
    return org, pes, tit


def _candidato(deal, score, opcao=None):
    org = deal.get("organization") or {}
    pes = deal.get("person") or {}
    etapa = (deal.get("dealStage") or {}).get("name")
    status = (deal.get("dealStatus") or {}).get("name")
    entrada = (deal.get("createTime") or "")[:10]
    c = {}
    if opcao is not None:
        c["opcao"] = opcao
    c.update({
        "dealId": deal.get("dealId"),
        "condominio": org.get("name"),
        "contato": pes.get("name"),
        "titulo": deal.get("title"),
        "etapa": etapa,
        "status": status,
        "entrada": entrada,
        "score": round(score, 1),
    })
    return c


def buscar(telefone, termo, limite=5, recente=False):
    """Retorna candidatos ranqueados dentro da carteira do consultor."""
    tel = so_digitos(telefone)
    consultor = CONSULTORES.get(tel)
    if not consultor:
        return {"erro": "consultor_nao_mapeado", "telefone": tel}, 422

    carteira = carteira_do_consultor(consultor["userId"])
    termo_n = normalizar(termo)

    # Nome do responsável vem do Agendor (o que reflete quem está na carteira hoje).
    nome_resp = _nome_responsavel(carteira, consultor["userId"])

    # Sem termo: devolve os mais recentes da carteira.
    if not termo_n:
        recentes = sorted(carteira, key=lambda d: d.get("createTime") or "", reverse=True)
        cands = [_candidato(d, 0.0, i) for i, d in enumerate(recentes[:limite], 1)]
        return {"status": "ok", "consultor": nome_resp, "total_carteira": len(carteira),
                "candidatos": cands}, 200

    pontuados = []
    for d in carteira:
        org, pes, tit = _campos_busca(d)
        alvos = [normalizar(x) for x in (org, tit, pes) if x]
        score = max((_pontuar(termo_n, a) for a in alvos), default=0)
        if recente and (d.get("createTime") or "") >= time.strftime(
            "%Y-%m-%d", time.gmtime(time.time() - 3 * 86400)
        ):
            score = min(100, score + 8)  # leve empurrão pra entradas recentes
        pontuados.append((score, d))

    pontuados.sort(key=lambda x: x[0], reverse=True)
    melhor = pontuados[0][0] if pontuados else 0

    base = {"consultor": nome_resp, "total_carteira": len(carteira)}

    # Nada chega nem perto: não localiza e não deixa gravar.
    if melhor < PISO_APROXIMADO:
        return {**base, "status": "nao_localizado", "candidatos": [],
                "mensagem": "Condomínio não localizado"}, 200

    # Tem match confiável: lista os candidatos pra confirmar.
    if melhor >= CORTE_LISTAR:
        topo = [(s, d) for s, d in pontuados[:limite] if s >= CORTE_LISTAR]
        cands = [_candidato(d, s, i) for i, (s, d) in enumerate(topo, 1)]
        return {**base, "status": "ok", "encontrados": len(cands), "candidatos": cands}, 200

    # Match fraco mas plausível: oferece só o mais aproximado pra confirmar.
    s, d = pontuados[0]
    return {**base, "status": "aproximado", "encontrados": 1,
            "candidatos": [_candidato(d, s, 1)]}, 200
