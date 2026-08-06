# Integração via Webhook (n8n)

## Visão Geral

Após cada voto confirmado, o sistema envia automaticamente um POST com JSON
para uma URL de webhook configurável, com os dados do associado e suas
escolhas. A integração nunca teve nada de específico de um destino — é um
`WebhookService` genérico (`app/integrations/webhook.py`) que faz um
`POST` de JSON com um Bearer token opcional. O destino atual é um workflow
no [n8n](https://n8n.io).

## Configuração

No `.env`:

```env
WEBHOOK_URL=https://seu-n8n.exemplo.com/webhook/SEU_WEBHOOK_ID
WEBHOOK_TOKEN=          # Obrigatório em produção — ver seção de segurança abaixo
WEBHOOK_RETRY_MAX=5
WEBHOOK_RETRY_DELAY_SECONDS=60
```

| Variável | Descrição |
|---|---|
| `WEBHOOK_URL` | URL do node **Webhook** do n8n que recebe o POST. Vazia = envio desligado (loga um aviso e não tenta enviar). |
| `WEBHOOK_TOKEN` | Token enviado como `Authorization: Bearer <token>`. Vazio = header não é enviado. |
| `WEBHOOK_RETRY_MAX` | Número máximo de tentativas de reenvio por voto antes do worker desistir dele. |
| `WEBHOOK_RETRY_DELAY_SECONDS` | Intervalo, em segundos, entre cada rodada do worker de retry em background. |

## Payload Enviado

```json
{
  "nome": "Fulano de Tal",
  "numero_socio": "0042",
  "cpf": "123.456.789-00",
  "telefone": "11988887777",
  "candidatos": ["Candidato A", "Candidato B"],
  "preferido": "Candidato A",
  "aceite_lgpd": true,
  "data": "2026-08-06T14:32:10.123456+00:00"
}
```

| Campo | Tipo | Descrição |
|-------|------|-----------|
| nome | string | Nome completo do associado |
| numero_socio | string | Número de sócio, sempre com 4 dígitos (zeros à esquerda preservados) |
| cpf | string | CPF formatado |
| telefone | string | Celular normalizado |
| candidatos | string[] | Nomes dos candidatos selecionados |
| preferido | string | Nome do candidato preferencial |
| aceite_lgpd | boolean | Consentimento LGPD |
| data | string | ISO 8601 da resposta |

## Configurar o Workflow no n8n

1. Crie um novo workflow e adicione um node **Webhook**.
2. Configure o método como **POST**.
3. Copie a **Production URL** gerada pelo node e cole em `WEBHOOK_URL` no
   `.env`.
4. Na aba **Authentication** do node, ative **Header Auth**, criando uma
   credencial cujo valor do header seja `Bearer <o mesmo valor de
   WEBHOOK_TOKEN>`. **Isto não é opcional em produção**: o backend sempre
   envia `Authorization: Bearer <WEBHOOK_TOKEN>` em cada requisição; sem o
   Header Auth correspondente no node, qualquer pessoa que descobrir a URL
   do webhook (ela não é secreta por natureza — pode vazar em logs de
   proxy, por exemplo) consegue postar requisições fabricadas para o
   workflow, e o node aceita nome, CPF e telefone de quem quer que a
   encontre.
5. Nas configurações do node, marque **Respond Immediately** (responder
   assim que a requisição chega, antes de processar o restante do
   workflow). O cliente HTTP do backend (`httpx.AsyncClient`, ver
   `app/integrations/webhook.py`) tem timeout de **30 segundos**. Se o
   workflow demorar mais que isso para responder, o voto do usuário fica
   preso esperando a resposta HTTP e o backend trata a falha como erro de
   envio — mesmo que o workflow tenha, na prática, recebido os dados
   corretamente.
6. A partir do node Webhook, monte o restante do workflow (gravar em uma
   planilha, mandar para um CRM, etc.) como um segundo passo, já
   desacoplado da resposta ao backend.

## Retry Automático

O sistema implementa retry em duas camadas:

1. **Imediato:** tenta enviar ao registrar o voto.
2. **Background worker:** `webhook_retry_worker` (`app/main.py`) roda em
   loop dentro do próprio processo da aplicação e reprocessa logs com
   status `pending` ou `failed` a cada `WEBHOOK_RETRY_DELAY_SECONDS`.

**Não é necessário nenhum workflow agendado de retry dentro do n8n.** O
reprocessamento já é responsabilidade do backend — criar uma automação de
retry no lado do n8n seria redundante e poderia até duplicar envios.

Cada tentativa é registrada na tabela `webhook_logs`:

| Campo | Descrição |
|-------|-----------|
| associado_id | ID do associado |
| payload | JSON serializado |
| status | pending / sent / failed |
| tentativas | Contador de tentativas |
| ultimo_erro | Mensagem do último erro |
| enviado_em | Timestamp do envio bem-sucedido |

## Retry Manual

No painel admin, aba **Exportar**, clique em **Reprocessar Webhook** ou
use:

```bash
curl -X POST http://localhost:8000/api/admin/webhook/retry \
  -H "Authorization: Bearer <token>"
```

Esta rota é um gatilho manual complementar ao worker automático — útil
para forçar uma tentativa imediata sem esperar o próximo ciclo do loop de
background.

## Testando Localmente

Use um serviço como [webhook.site](https://webhook.site) para capturar
payloads sem precisar subir um n8n local:

```env
WEBHOOK_URL=https://webhook.site/seu-uuid
```

Submeta um voto e verifique o payload recebido.

## Monitoramento

Consulte logs da aplicação:

```bash
docker compose logs -f app | grep -i webhook
```

Logs de falha incluem detalhes do erro HTTP para diagnóstico.
