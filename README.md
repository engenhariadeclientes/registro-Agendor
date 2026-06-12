# Webhook Comercial — BotConversa ↔ Agendor

Serviço Flask (Railway) com três responsabilidades:

- `POST /webhook/agendor` — grava o comentário confirmado no card do negócio.
- `POST /buscar` — acha o lead na carteira do consultor (busca por nome, fuzzy).
- `GET  /health` — checagem simples.

Sem banco de dados. A busca lê o Agendor ao vivo (com cache curto em memória).

## Deploy (Railway)
1. Suba esta pasta num repositório no GitHub.
2. Railway → New Project → Deploy from GitHub repo.
3. Variables:
   - `AGENDOR_TOKEN` — token de admin da API do Agendor (Menu > Integrações).
   - `WEBHOOK_SECRET` — um segredo que você inventa (mesmo valor no BotConversa).
4. Endpoints na URL pública gerada.

## Antes de ligar a busca: o de-para de consultores
Em `buscar.py`, preencha `CONSULTORES` com cada consultor:
```python
CONSULTORES = {
    "5511999990000": {"userId": 70, "nome": "Marina Souza"},
}
```
A chave é o WhatsApp do consultor (só dígitos); `userId` é o id dele no Agendor
(rota `GET /users` lista os usuários da conta).

## POST /buscar
Corpo:
```json
{ "telefone": "5511999990000", "termo": "joão jardim das flores", "recente": true }
```
Resposta: campo `status` + candidatos só da carteira do consultor, com dados de
desempate (condomínio, contato, etapa, status do negócio, data de entrada, dealId):
- `status: "ok"` — match confiável; a IA mostra os candidatos pra confirmar.
- `status: "aproximado"` — sem match forte; oferece só o mais parecido pra confirmar.
- `status: "nao_localizado"` — nada plausível; resposta traz `mensagem: "Condomínio não localizado"` e a IA NÃO grava.
A IA mostra os candidatos e o consultor escolhe — nunca escolhe sozinha por nome.
Cortes ajustáveis no topo de `buscar.py`: `CORTE_LISTAR` (70) e `PISO_APROXIMADO` (55).

## POST /webhook/agendor
Dispara depois da confirmação do consultor; grava o comentário no `dealId` escolhido.
Header obrigatório em ambos: `x-webhook-secret: <WEBHOOK_SECRET>`.

## Ainda não incluído (enriquecimento)
- Cidade (mora no cadastro da empresa) e origem (campo customizado) não vêm no
  negócio; entram numa próxima volta, com chamada extra / mapeamento do campo.
- Extração do texto bagunçado (`/extrair`, Sonnet + pydantic): próxima peça.
