#!/usr/bin/env python3
"""Crée la stack dans le dossier courant : python /chemin/create_project.py [--force]."""

import argparse
import sys
from pathlib import Path


HOSTS = (
    "frontend.apex.tld",
    "backend.apex.tld",
    "grafana.apex.tld",
    "n8n.apex.tld",
    "ckpit.apex.tld",
)

TLS_COMMANDS = r"""openssl req -x509 -newkey rsa:4096 -sha256 -days 365 -nodes \
  -keyout certs/apex.tld.key \
  -out certs/apex.tld.crt \
  -subj "/CN=*.apex.tld" \
  -addext "subjectAltName=DNS:*.apex.tld,DNS:apex.tld"

for service in frontend backend grafana n8n ckpit; do
  cp certs/apex.tld.crt "certs/${service}.apex.tld.crt"
  cp certs/apex.tld.key "certs/${service}.apex.tld.key"
done
chmod 600 certs/*.key"""

DOCKER_COMMANDS = """docker compose up -d --build

docker compose ps

curl -k https://frontend.apex.tld
curl -k https://backend.apex.tld/health
curl -k https://grafana.apex.tld
curl -k https://n8n.apex.tld
curl -k https://ckpit.apex.tld

docker compose down

docker compose down -v"""

HOSTS_ENTRIES = "\n".join(f"127.0.0.1 {host}" for host in HOSTS)
URL_LIST = "\n".join(f"- https://{host}" for host in HOSTS)
CERTIFICATE_NAMES = "\n".join(
    f"{host}.{extension}" for host in HOSTS for extension in ("crt", "key")
)

README = f"""# Single-host reverse proxy stack

Cette stack représente une seule machine qui héberge plusieurs services web
Docker derrière nginx-proxy.

## Prérequis

- Docker Engine et Docker Compose 2.23.1 ou supérieur.
- Les ports 80 et 443 disponibles sur la machine.
- OpenSSL pour générer les certificats locaux.

## Sous-domaines

{URL_LIST}

## DNS et utilisation locale

Tous les sous-domaines pointent vers la même machine. En déploiement, leurs
enregistrements DNS pointent vers l'adresse IP de cette machine.

En local, ajouter ces lignes à `/etc/hosts` sur la machine cliente :

```text
{HOSTS_ENTRIES}
```

Ces adresses locales conviennent lorsque Docker tourne sur la même machine.
Pour une machine Docker distante, utiliser son adresse IP à la place de
`127.0.0.1`.

## Reverse proxy

1. Le DNS ou `/etc/hosts` amène la requête jusqu'à la machine.
2. nginx-proxy reçoit la requête sur les ports 80/443 et termine TLS sur 443.
3. nginx-proxy lit le header HTTP `Host`.
4. nginx-proxy route vers le conteneur qui déclare le `VIRTUAL_HOST`
   correspondant, sur son port HTTP interne.

Tous les services appartiennent au réseau Docker `app_net`. Seul nginx-proxy
publie des ports sur la machine. PostgreSQL est accessible par n8n à
`postgres:5432` sur ce réseau interne.

Le frontend sert une page statique avec nginx:alpine. L'API Flask du backend
écoute sur `0.0.0.0:5000` via Gunicorn. `GET /` décrit le service et
`GET /health` retourne `{{"status": "healthy"}}`.

n8n attend que PostgreSQL soit prêt et déclare `N8N_PROXY_HOPS=1`.
La configuration Cockpit est intégrée à `docker-compose.yml` via `configs` :
elle autorise HTTP entre nginx-proxy et Cockpit et déclare l'origine HTTPS
publique ainsi que `X-Forwarded-Proto`. Cela nécessite Compose 2.23.1 ou supérieur.
Cockpit fonctionne en mode bastion : la connexion à une machine administrée
se fait par SSH avec les identifiants de cette machine.

## Certificats TLS

Les certificats TLS sont attendus dans `certs/`. Les noms de fichiers attendus
par nginx-proxy sont :

```text
{CERTIFICATE_NAMES}
```

Le dossier contient initialement uniquement `.gitkeep`. Préparer les
certificats avant de démarrer la stack pour que les URLs HTTPS fonctionnent.

Commande exemple pour générer un certificat wildcard auto-signé utilisable
localement, puis copier le couple `apex.tld.crt`/`apex.tld.key` vers les noms
attendus pour chaque sous-domaine :

```sh
{TLS_COMMANDS}
```

Le certificat couvre `*.apex.tld` et `apex.tld`. `curl -k` accepte ce certificat
auto-signé lors des vérifications locales. Le navigateur demande de faire
confiance au certificat pour accéder aux interfaces HTTPS.

Après remplacement des certificats d'une stack déjà démarrée, exécuter
`docker compose restart nginx-proxy`.

## Commandes Docker

Démarrer et construire les images :

```sh
docker compose up -d --build
```

Vérifier les services :

```sh
docker compose ps
```

Tester les URLs :

```sh
curl -k https://frontend.apex.tld
curl -k https://backend.apex.tld/health
curl -k https://grafana.apex.tld
curl -k https://n8n.apex.tld
curl -k https://ckpit.apex.tld
```

Arrêter les services en conservant les données :

```sh
docker compose down
```

Arrêter les services et supprimer les volumes de données :

```sh
docker compose down -v
```

Les volumes persistants sont `grafana_data`, `n8n_data` et `postgres_data`.
Grafana est initialisé avec `admin`/`admin`. PostgreSQL utilise la base `n8n`
et les identifiants `n8n`/`n8n` demandés pour cette stack.

## Documentation

- [nginx-proxy](https://github.com/nginx-proxy/nginx-proxy/blob/main/docs/README.md)
- [Configurations Compose](https://docs.docker.com/reference/compose-file/configs/)
- [Cockpit derrière NGINX](https://cockpit-project.org/external/wiki/Proxying-Cockpit-over-NGINX)
"""

FILES = {
    ".gitignore": """certs/*
!certs/.gitkeep
__pycache__/
*.py[cod]
.venv/
.env
""",
    "README.md": README,
    "certs/.gitkeep": "",
    "docker-compose.yml": """services:
  nginx-proxy:
    image: nginxproxy/nginx-proxy:latest
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - /var/run/docker.sock:/tmp/docker.sock:ro
      - ./certs:/etc/nginx/certs:ro
    networks:
      - app_net

  frontend:
    build: ./frontend
    restart: unless-stopped
    environment:
      VIRTUAL_HOST: frontend.apex.tld
      VIRTUAL_PROTO: http
    expose:
      - "80"
    networks:
      - app_net

  backend:
    build: ./backend
    restart: unless-stopped
    environment:
      VIRTUAL_HOST: backend.apex.tld
      VIRTUAL_PORT: "5000"
      VIRTUAL_PROTO: http
    expose:
      - "5000"
    networks:
      - app_net

  grafana:
    image: grafana/grafana-oss:latest
    restart: unless-stopped
    environment:
      VIRTUAL_HOST: grafana.apex.tld
      VIRTUAL_PORT: "3000"
      VIRTUAL_PROTO: http
      GF_SECURITY_ADMIN_USER: admin
      GF_SECURITY_ADMIN_PASSWORD: admin
      GF_SERVER_ROOT_URL: https://grafana.apex.tld
    expose:
      - "3000"
    volumes:
      - grafana_data:/var/lib/grafana
    networks:
      - app_net

  n8n:
    image: n8nio/n8n:latest
    restart: unless-stopped
    environment:
      VIRTUAL_HOST: n8n.apex.tld
      VIRTUAL_PORT: "5678"
      VIRTUAL_PROTO: http
      N8N_HOST: n8n.apex.tld
      N8N_PROTOCOL: https
      WEBHOOK_URL: https://n8n.apex.tld/
      N8N_PROXY_HOPS: "1"
      DB_TYPE: postgresdb
      DB_POSTGRESDB_HOST: postgres
      DB_POSTGRESDB_PORT: "5432"
      DB_POSTGRESDB_DATABASE: n8n
      DB_POSTGRESDB_USER: n8n
      DB_POSTGRESDB_PASSWORD: n8n
    expose:
      - "5678"
    volumes:
      - n8n_data:/home/node/.n8n
    depends_on:
      postgres:
        condition: service_healthy
    networks:
      - app_net

  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: n8n
      POSTGRES_USER: n8n
      POSTGRES_PASSWORD: n8n
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U n8n -d n8n"]
      interval: 5s
      timeout: 5s
      retries: 20
      start_period: 10s
    networks:
      - app_net

  ckpit:
    image: quay.io/cockpit/ws:latest
    restart: unless-stopped
    environment:
      VIRTUAL_HOST: ckpit.apex.tld
      VIRTUAL_PORT: "9090"
      VIRTUAL_PROTO: http
    expose:
      - "9090"
    configs:
      - source: ckpit_config
        target: /etc/cockpit/cockpit.conf
    networks:
      - app_net

networks:
  app_net:
    driver: bridge

volumes:
  grafana_data:
  n8n_data:
  postgres_data:

configs:
  ckpit_config:
    content: |
      [WebService]
      Origins = https://ckpit.apex.tld wss://ckpit.apex.tld
      ProtocolHeader = X-Forwarded-Proto
      AllowUnencrypted = true
      RequireHost = true
""",
    "frontend/Dockerfile": """FROM nginx:alpine
COPY index.html /usr/share/nginx/html/index.html
EXPOSE 80
""",
    "frontend/index.html": """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Frontend</title>
  <style>
    body { font-family: sans-serif; max-width: 42rem; margin: 5rem auto; padding: 1rem; }
    h1 { margin-bottom: 1rem; }
  </style>
</head>
<body>
  <h1>Frontend</h1>
  <p>frontend.apex.tld</p>
  <p>Routed by nginx-proxy</p>
</body>
</html>
""",
    "backend/Dockerfile": """FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
""",
    "backend/requirements.txt": "Flask==3.1.3\ngunicorn==26.2.0\n",
    "backend/app.py": """from flask import Flask, jsonify

app = Flask(__name__)
app.json.sort_keys = False


@app.get("/")
def index():
    return jsonify(
        service="backend",
        url="https://backend.apex.tld",
        status="ok",
        routed_by="nginx-proxy",
    )


@app.get("/health")
def health():
    return jsonify(status="healthy")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
""",
}


def main():
    parser = argparse.ArgumentParser(
        description="Crée une stack Docker Compose dans le dossier courant."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Autorise l'écrasement des fichiers du projet déjà présents.",
    )
    args = parser.parse_args()
    root = Path.cwd()

    try:
        # Vérifier toutes les destinations avant la première écriture.
        errors = []
        for directory in ("certs", "frontend", "backend"):
            path = root / directory
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                errors.append(f"Dossier attendu, sans lien symbolique : {directory}")

        for relative in FILES:
            path = root / relative
            if path.is_symlink():
                errors.append(f"Lien symbolique refusé : {relative}")
            elif path.exists():
                if not path.is_file():
                    errors.append(f"Fichier ordinaire attendu : {relative}")
                elif not args.force:
                    errors.append(f"Fichier existant : {relative} (utiliser --force)")

        if errors:
            parser.error("Aucun fichier écrit.\n" + "\n".join(errors))

        for relative, content in FILES.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            existed = path.exists()
            mode = "w" if args.force else "x"
            with path.open(mode, encoding="utf-8", newline="\n") as stream:
                stream.write(content)
            action = "Mis à jour" if existed else "Créé"
            print(f"{action} : {relative}")
    except OSError as exc:
        print(f"Erreur de génération : {exc}", file=sys.stderr)
        return 1

    print(f"\nProjet créé dans : {root}")
    print("\nAjouter ces lignes à /etc/hosts pour un Docker local :")
    print(HOSTS_ENTRIES)
    print("\nCommandes utiles à exécuter dans ce dossier :")
    print("\n# Préparer les certificats TLS avant le démarrage")
    print(TLS_COMMANDS)
    print("\n# Démarrer, vérifier, puis arrêter la stack")
    print(DOCKER_COMMANDS)
    print("\nLa commande docker compose down -v supprime les volumes de données.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
