# Integração com Pipefy

## Visão Geral

Após cada voto confirmado, o sistema envia automaticamente um webhook para o Pipefy com os dados do associado e suas escolhas.

## Configuração

No `.env`:

```env
PIPEFY_WEBHOOK_URL=https://hooks.pipefy.com/workflows/SEU_WEBHOOK_ID
PIPEFY_API_TOKEN=          # Opcional, se o webhook exigir Bearer token
PIPEFY_RETRY_MAX=5
PIPEFY_RETRY_DELAY_SECONDS=60
```

## Payload Enviado

```json
{
  "nome": "João Silva",
  "cpf": "123.456.789-09",
  "telefone": "11999998888",
  "candidatos": ["Maria Santos", "Pedro Oliveira"],
  "preferido": "Maria Santos",
  "aceite_lgpd": true,
  "data": "2026-08-01T14:30:00+00:00"
}
```

| Campo | Tipo | Descrição |
|-------|------|-----------|
| nome | string | Nome completo do associado |
| cpf | string | CPF formatado |
| telefone | string | Celular normalizado |
| candidatos | string[] | Nomes dos candidatos selecionados |
| preferido | string | Nome do candidato preferencial |
| aceite_lgpd | boolean | Consentimento LGPD |
| data | string | ISO 8601 da resposta |

## Configurar Webhook no Pipefy

### Opção 1: Pipefy Automation (Webhook)

1. No Pipefy, crie uma automação do tipo **Webhook**
2. Configure para receber POST com JSON
3. Mapeie os campos do payload para os campos do card/pipe

### Opção 2: Pipefy API (GraphQL)

Se preferir criar cards via API GraphQL em vez de webhook:

1. Obtenha um Personal Access Token em **Account Settings > Developer Tools**
2. Use a mutation `createCard` apontando para seu pipe
3. Adapte `app/integrations/pipefy.py` para usar GraphQL (extensível)

O webhook é o método padrão e recomendado por simplicidade.

## Retry Automático

O sistema implementa retry em duas camadas:

1. **Imediato:** tenta enviar ao registrar o voto
2. **Background worker:** reprocessa logs com status `pending` ou `failed` a cada 60s (configurável)

Cada tentativa é registrada na tabela `pipefy_logs`:

| Campo | Descrição |
|-------|-----------|
| associado_id | ID do associado |
| payload | JSON serializado |
| status | pending / sent / failed |
| tentativas | Contador de tentativas |
| ultimo_erro | Mensagem do último erro |
| enviado_em | Timestamp do envio bem-sucedido |

## Retry Manual

No painel admin, aba **Exportar**, clique em **Reprocessar Pipefy** ou use:

```bash
curl -X POST http://localhost:8000/api/admin/pipefy/retry \
  -H "Authorization: Bearer <token>"
```

## Testando Localmente

Use um serviço como [webhook.site](https://webhook.site) para capturar payloads:

```env
PIPEFY_WEBHOOK_URL=https://webhook.site/seu-uuid
```

Submeta um voto e verifique o payload recebido.

## Monitoramento

Consulte logs da aplicação:

```bash
docker compose logs -f app | grep -i pipefy
```

Logs de falha incluem detalhes do erro HTTP para diagnóstico.
