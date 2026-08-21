# Guia de Deploy em Produção

## Compatibilidade

Testado e compatível com:
- VPS Linux (Ubuntu/Debian)
- AWS (EC2 + RDS + ElastiCache)
- Render
- DigitalOcean (Droplet + Managed DB)
- Hostinger VPS
- Oracle Cloud (Always Free tier)

## Checklist Pré-Deploy

- [ ] `SECRET_KEY` com 64+ caracteres aleatórios
- [ ] `ADMIN_PASSWORD` com hash bcrypt
- [ ] `DEBUG=false`
- [ ] `HTTPS_ONLY=true`
- [ ] OTP provider configurado (não usar `mock`)
- [ ] reCAPTCHA v3 com domínio de produção
- [ ] Backups do PostgreSQL configurados

## Deploy com Docker (VPS)

### 1. Servidor

```bash
# Ubuntu 22.04+
# docker-compose-plugin NÃO está no repositório padrão do Ubuntu — testado
# em 24.04 e "apt install docker.io docker-compose-plugin" falha com
# "Unable to locate package docker-compose-plugin". Precisa do repositório
# oficial do Docker:
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin nginx certbot python3-certbot-nginx ufw
sudo usermod -aG docker $USER   # relogue (ou "newgrp docker") para valer

sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw --force enable
```

### 2. Clone e configure

```bash
git clone <repo> /opt/sondagem-clube
cd /opt/sondagem-clube
cp .env.example .env
nano .env  # Configure todas as variáveis

# O container "app" roda como usuário não-root (uid 999, ver Dockerfile).
# uploads/ acabou de ser criado pelo "git clone" e pertence a quem clonou
# — o container não consegue escrever nele e cai num loop de restart com
# "PermissionError: [Errno 13] Permission denied: 'uploads/candidatos'".
# Alinhe o dono ANTES do primeiro "docker compose up":
sudo chown -R 999:999 uploads
```

### 3. Suba

```bash
docker compose up -d --build
docker compose exec app alembic upgrade head
```

### 4. Nginx + TLS

```bash
sudo cp deploy/nginx/sempretricolor.org.conf /etc/nginx/sites-available/sondagem
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -sf /etc/nginx/sites-available/sondagem /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx && sudo systemctl enable nginx

# Só depois que o DNS já apontar para este servidor (senão a validação do
# domínio falha):
sudo certbot --nginx -d seu-dominio -d www.seu-dominio \
  --non-interactive --agree-tos -m seu-email@exemplo.com
```

### 4b. Aplicar o prefixo /pesquisa2026 num servidor que JÁ tem TLS

A sondagem não mora mais na raiz do domínio: ela é servida sob
`/pesquisa2026`, e a raiz fica reservada para o site institucional.

**Não copie `deploy/nginx/sempretricolor.org.conf` por cima do arquivo do
servidor que já passou pelo certbot.** O certbot reescreveu aquele arquivo
com os blocos 443, o caminho do certificado e o redirect de 80 para 443 —
nada disso está no repositório, e sobrescrever derruba o HTTPS do site.

Num servidor já configurado, edite o arquivo de lá e mexa só nos
`location` do bloco `server` que escuta 443:

```bash
sudo nano /etc/nginx/sites-available/sempretricolor.org
```

Dentro do `server { listen 443 ssl; ... }`, troque o `location /` que
existe hoje por estes três:

```nginx
    # PROVISÓRIO: enquanto o site institucional não existe, a raiz leva
    # para a sondagem (o link já circulou como sempretricolor.org).
    # Apague quando o site institucional subir.
    location = / {
        return 302 /pesquisa2026/;
    }

    # Sem barra no fim: o prefixo precisa chegar inteiro na aplicação.
    location /pesquisa2026 {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        return 404;
    }
```

E no `.env` da aplicação, o prefixo do outro lado — os dois PRECISAM
concordar, senão o Nginx encaminha um caminho que nenhuma rota atende:

```bash
cd /opt/sempretricolor

# APP_PATH_PREFIX: acrescenta se não existir, troca se já existir
grep -q '^APP_PATH_PREFIX=' .env \
  && sed -i 's|^APP_PATH_PREFIX=.*|APP_PATH_PREFIX=/pesquisa2026|' .env \
  || echo 'APP_PATH_PREFIX=/pesquisa2026' >> .env

# ALLOWED_ORIGINS é uma ORIGEM, não uma URL: sem barra no fim, senão a
# comparação do CORS nunca casa
sed -i 's|^ALLOWED_ORIGINS=.*|ALLOWED_ORIGINS=https://sempretricolor.org|' .env

docker compose up -d --build app
sudo nginx -t && sudo systemctl reload nginx
```

Verifique os dois lados antes de avisar os sócios:

```bash
curl -f http://localhost:8000/health                           # raiz, de dentro do container
curl -sI https://sempretricolor.org/ | head -1                 # 302 para /pesquisa2026/
curl -f https://sempretricolor.org/pesquisa2026/               # fluxo público
curl -f https://sempretricolor.org/pesquisa2026/admin          # painel
curl -sI https://sempretricolor.org/qualquer-outra | head -1   # 404
```

O `/health` continua respondendo **na raiz** de propósito: é lá que o
HEALTHCHECK do container bate, de dentro, sem passar pelo Nginx. Ele
também responde em `/pesquisa2026/health`, que é o endereço para um
monitor externo de uptime — pelo domínio, a raiz não é mais nossa.

### 4. Reverse Proxy (Nginx)

```nginx
server {
    listen 80;
    server_name sondagem.seuclube.com.br;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name sondagem.seuclube.com.br;

    ssl_certificate /etc/letsencrypt/live/sondagem.seuclube.com.br/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/sondagem.seuclube.com.br/privkey.pem;

    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo certbot --nginx -d sondagem.seuclube.com.br
```

## Deploy no Render

1. Crie **Web Service** apontando para o repositório
2. Build: `docker build -t app .`
3. Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Adicione **PostgreSQL** e **Redis** como serviços gerenciados
5. Configure variáveis de ambiente no dashboard

## Deploy na AWS

```
┌─────────────┐     ┌──────────┐     ┌─────────────┐
│  ALB/HTTPS  │────▶│  EC2/ECS │────▶│     RDS     │
└─────────────┘     │  FastAPI │     │ PostgreSQL  │
                    └────┬─────┘     └─────────────┘
                         │
                    ┌────▼─────┐
                    │ElastiCache│
                    │   Redis   │
                    └──────────┘
```

- EC2: t3.small ou superior
- RDS: db.t3.micro (PostgreSQL 16)
- ElastiCache: cache.t3.micro (Redis 7)

## Variáveis de Produção

```env
APP_ENV=production
DEBUG=false
HTTPS_ONLY=true
ALLOWED_ORIGINS=https://sondagem.seuclube.com.br

DATABASE_URL=postgresql+asyncpg://user:pass@db-host:5432/sondagem
DATABASE_URL_SYNC=postgresql://user:pass@db-host:5432/sondagem
REDIS_URL=redis://redis-host:6379/0

OTP_PROVIDER=twilio
RECAPTCHA_SITE_KEY=...
RECAPTCHA_SECRET_KEY=...
```

## Backup

```bash
# Backup PostgreSQL
docker compose exec db pg_dump -U postgres sondagem_clube > backup_$(date +%Y%m%d).sql

# Restore
cat backup.sql | docker compose exec -T db psql -U postgres sondagem_clube
```

## Monitoramento

- Health check: `GET /health` (configure no load balancer)
- Logs: `docker compose logs -f app`

## Atualizações

```bash
cd /opt/sondagem-clube
git pull
docker compose up -d --build
docker compose exec app alembic upgrade head
```

## Segurança em Produção

1. Firewall: abra apenas 80/443
2. Não exponha PostgreSQL/Redis publicamente
3. Rotacione `SECRET_KEY` e tokens periodicamente
4. Monitore logs de auditoria (`audit_logs`)
5. Configure fail2ban para proteção adicional
