#!/usr/bin/env python3
"""Génère la stack Apex Fullstack dans le dossier courant, avec --force facultatif."""

import argparse
import sys
from pathlib import Path

HOSTS_ENTRIES = '127.0.0.1 frontend.apex.tld\n127.0.0.1 backend.apex.tld\n127.0.0.1 grafana.apex.tld\n127.0.0.1 n8n.apex.tld\n127.0.0.1 ckpit.apex.tld'

TLS_COMMANDS = 'openssl req -x509 -newkey rsa:4096 -sha256 -days 365 -nodes \\\n  -keyout certs/apex.tld.key \\\n  -out certs/apex.tld.crt \\\n  -subj "/CN=*.apex.tld" \\\n  -addext "subjectAltName=DNS:*.apex.tld,DNS:apex.tld"\n\nfor service in frontend backend grafana n8n ckpit; do\n  cp certs/apex.tld.crt "certs/${service}.apex.tld.crt"\n  cp certs/apex.tld.key "certs/${service}.apex.tld.key"\ndone\nchmod 600 certs/*.key'

NEXT_COMMANDS = 'docker compose config --quiet\n\ndocker compose up -d --build\n\ndocker compose ps\n\ncurl -k https://frontend.apex.tld\ncurl -k https://backend.apex.tld/health\ncurl -k https://backend.apex.tld/items\ncurl -k https://grafana.apex.tld\ncurl -k https://n8n.apex.tld\ncurl -k https://ckpit.apex.tld\n\ndocker compose logs --tail=20 worker\n\ndocker compose down\n\ndocker compose down -v'

OBSOLETE_FILES = ("backend/app.py",)

FILES = {
    '.gitignore': r'''certs/*
!certs/.gitkeep
__pycache__/
*.py[cod]
.venv/
.env
.idea/
node_modules/
dist/
.pytest_cache/
backups/
''',
    'README.md': r'''# Apex Fullstack — stack self-hosted derrière nginx-proxy

Cette stack héberge une vraie application et ses services sur **une seule VM,
avec une seule IP publique et plusieurs sous-domaines**. Vite fournit le frontend,
FastAPI expose l'API, PostgreSQL conserve les items et Redis accélère leur lecture.
Un worker écrit un message en base toutes les 30 secondes.

n8n et Grafana sont des instances locales. n8n utilise sa propre base PostgreSQL.
Grafana dispose automatiquement d'une datasource Prometheus locale. `ckpit` est
un dashboard applicatif FastAPI/Jinja2 qui vérifie les composants sur le réseau
Docker et affiche les résultats avec l'horodatage du rendu.

## Services et réseau

Tous les conteneurs appartiennent au réseau bridge `app_net`. Seul nginx-proxy
publie les ports de la VM : **80 et 443**. Les autres services utilisent `expose`
pour leurs ports internes ; le worker n'écoute sur aucun port.

| Service public | URL | Port HTTP interne |
| --- | --- | --- |
| frontend | https://frontend.apex.tld | 80 |
| backend | https://backend.apex.tld | 8000 |
| grafana | https://grafana.apex.tld | 3000 |
| n8n | https://n8n.apex.tld | 5678 |
| ckpit | https://ckpit.apex.tld | 8000 |

Ces services déclarent leur `VIRTUAL_HOST`, `VIRTUAL_PORT` et `VIRTUAL_PROTO=http`.
nginx-proxy utilise le socket Docker monté en lecture seule pour détecter leurs
conteneurs et construire sa configuration automatiquement. TLS se termine au proxy.

| Service interne, sans VIRTUAL_HOST | Rôle et adresse Docker |
| --- | --- |
| postgres | Base `app` du backend et du worker, `postgres:5432` |
| redis | Cache des 20 derniers items, `redis:6379` |
| worker | Insère un item immédiatement, puis toutes les 30 secondes |
| n8n-postgres | Base `n8n` dédiée, `n8n-postgres:5432` |
| prometheus | Scrape `backend:8000/metrics` toutes les 5 secondes, écoute sur `prometheus:9090` |

Les noms de services ci-dessus sont résolus par le DNS Docker. PostgreSQL, Redis
et Prometheus n'ont ni ports publiés ni sous-domaines publics.

## DNS et reverse proxy

Les cinq sous-domaines ont des enregistrements DNS qui pointent vers **la même
IP publique**. Le DNS sélectionne la machine ; il ne sélectionne pas le conteneur.

1. Le DNS ou `/etc/hosts` amène le client jusqu'à la VM.
2. nginx-proxy reçoit la requête sur 80/443.
3. Sur 443, il présente le certificat TLS correspondant au sous-domaine.
4. Il lit le header HTTP `Host` et route vers le conteneur qui déclare le
   `VIRTUAL_HOST` correspondant, sur son `VIRTUAL_PORT` interne.

En local, ajouter ces lignes à `/etc/hosts` sur la machine qui utilise le
navigateur et curl, lorsque Docker tourne sur cette même machine :

```text
127.0.0.1 frontend.apex.tld
127.0.0.1 backend.apex.tld
127.0.0.1 grafana.apex.tld
127.0.0.1 n8n.apex.tld
127.0.0.1 ckpit.apex.tld
```

Pour une VM distante, remplacer `127.0.0.1` par son IP. La résolution des noms
internes entre conteneurs reste gérée par Docker.

## Certificats locaux

Prérequis : Docker Engine, Docker Compose v2 et OpenSSL. Le dossier `certs/`
contient initialement `.gitkeep`. Créer les certificats **avant** le démarrage
pour activer les URLs HTTPS :

```sh
openssl req -x509 -newkey rsa:4096 -sha256 -days 365 -nodes \
  -keyout certs/apex.tld.key \
  -out certs/apex.tld.crt \
  -subj "/CN=*.apex.tld" \
  -addext "subjectAltName=DNS:*.apex.tld,DNS:apex.tld"

for service in frontend backend grafana n8n ckpit; do
  cp certs/apex.tld.crt "certs/${service}.apex.tld.crt"
  cp certs/apex.tld.key "certs/${service}.apex.tld.key"
done
chmod 600 certs/*.key
```

nginx-proxy attend les couples suivants dans `certs/` :

```text
frontend.apex.tld.crt
frontend.apex.tld.key
backend.apex.tld.crt
backend.apex.tld.key
grafana.apex.tld.crt
grafana.apex.tld.key
n8n.apex.tld.crt
n8n.apex.tld.key
ckpit.apex.tld.crt
ckpit.apex.tld.key
```

`curl -k` accepte ce certificat auto-signé pour les tests locaux. Pour le
navigateur, établir la confiance dans le certificat pour **frontend et backend** :
le JavaScript appelle directement `https://backend.apex.tld/health` et `/items`.
Ouvrir `https://backend.apex.tld/health` permet de traiter sa confiance TLS avant
d'utiliser le frontend. L'API autorise l'origine `https://frontend.apex.tld` via CORS.

Après remplacement de certificats sur une stack démarrée :

```sh
docker compose restart nginx-proxy
```

## Lancement et tests HTTP

Depuis la racine du projet :

```sh
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

Le backend attend les healthchecks PostgreSQL et Redis. n8n attend sa base dédiée.
Le backend et le worker créent automatiquement la même table `items`, avec un
verrou de transaction qui sérialise leur initialisation simultanée.

```sh
curl -k https://frontend.apex.tld
curl -k https://backend.apex.tld/health
curl -k https://backend.apex.tld/items
curl -k https://grafana.apex.tld
curl -k https://n8n.apex.tld
curl -k https://ckpit.apex.tld
```

Créer puis relire un item :

```sh
curl -k -X POST https://backend.apex.tld/items \
  -H 'Content-Type: application/json' \
  -d '{"message":"Bonjour depuis Apex"}'
curl -k https://backend.apex.tld/items
curl -k https://backend.apex.tld/metrics
docker compose logs --tail=20 worker
```

L'API expose :

| Endpoint | Comportement |
| --- | --- |
| `GET /` | Identifie le backend, son URL, PostgreSQL et Redis |
| `GET /health` | Vérifie une requête SQL et un PING Redis ; HTTP 200 si les deux réussissent, 503 sinon |
| `GET /items` | Retourne les 20 derniers items, du plus récent au plus ancien |
| `POST /items` | Enregistre un message non vide de 1 à 500 caractères ; HTTP 201 |
| `GET /metrics` | Expose les compteurs, durées HTTP et états des dépendances au format Prometheus |
| `GET /docs` | Documentation OpenAPI interactive |

Redis contient le cache de `/items` avec un TTL de 5 secondes. Les écritures API
invalident ce cache ; les messages du worker deviennent visibles au plus tard à
l'expiration du cache. Une panne de PostgreSQL ou Redis est signalée explicitement.
Une lecture déjà présente dans Redis reste une lecture du cache pendant son TTL.
Si l'invalidation Redis échoue lors d'un POST, la transaction SQL est annulée.

Le frontend affiche l'état du backend, les derniers items, un formulaire de
création et les liens vers les autres interfaces. Il actualise les données toutes
les 15 secondes et affiche les erreurs de requête.

## Grafana, Prometheus et ckpit

Grafana est accessible avec les identifiants initiaux `admin` / `admin`.
La datasource **Prometheus** est provisionnée depuis
`grafana/provisioning/datasources/prometheus.yml` et utilise
`http://prometheus:9090` en mode proxy.

Dans Grafana → Explore → Prometheus, essayer :

```promql
up{job="backend"}
backend_database_up
backend_redis_up
rate(backend_http_requests_total[1m])
backend_cache_hits_total
```

Prometheus est local, avec la configuration `prometheus/prometheus.yml`. Ses
données ne disposent pas d'un volume persistant dans cette stack ; elles sont
perdues lorsque son conteneur est recréé.

`https://ckpit.apex.tld` affiche les cinq URLs publiques et interroge en parallèle :

- `http://frontend:80`
- `http://backend:8000/health`, dont les résultats PostgreSQL et Redis
- `http://n8n:5678`
- `http://grafana:3000/api/health`
- `http://prometheus:9090/-/healthy`
- `http://prometheus:9090/api/v1/targets?state=active`, pour vérifier que la target
  `http://backend:8000/metrics` est réellement UP

Le dashboard affiche l'horodatage UTC et se recharge toutes les 15 secondes.
Si le backend ne répond pas, ses dépendances sont affichées comme **non mesurées**.
La page reste accessible pour afficher les composants en échec.

n8n utilise `n8n-postgres` avec la base et les identifiants `n8n` / `n8n`.
Son URL publique et son URL de webhook sont configurées en HTTPS, avec un seul
proxy déclaré via `N8N_PROXY_HOPS=1`. Terminer la création du compte propriétaire
dans son interface lors du premier accès.

## Données et arrêt

| Volume | Contenu |
| --- | --- |
| postgres_data | Base applicative `app`, identifiants `app` / `app` |
| redis_data | Persistance Redis AOF |
| n8n_data | Configuration et clé de chiffrement n8n |
| n8n_postgres_data | Base dédiée n8n |
| grafana_data | Configuration et données Grafana |

Arrêter en conservant les données :

```sh
docker compose down
```

Supprimer aussi les cinq volumes et leurs données :

```sh
docker compose down -v
```

### Mise à niveau d'une ancienne stack déjà utilisée

L'ancienne version utilisait `postgres_data` pour n8n. Une base PostgreSQL déjà
initialisée n'est pas recréée lorsque ses variables `POSTGRES_*` changent.
Conserver le même nom de projet Compose et les volumes `n8n_data` et `postgres_data`.
Avant de remplacer l'ancien Compose, exporter n8n puis arrêter les conteneurs :

```sh
mkdir -p backups
docker compose exec -T postgres pg_dump -U n8n -d n8n > backups/n8n-before-upgrade.sql
docker compose down
```

Après mise à jour des fichiers, créer la base applicative dans l'ancien serveur
et restaurer n8n dans son nouveau serveur dédié :

```sh
docker compose up -d --wait postgres n8n-postgres
docker compose exec -T postgres psql -U n8n -d postgres \
  -c "CREATE ROLE app LOGIN PASSWORD 'app';"
docker compose exec -T postgres createdb -U n8n -O app app
docker compose exec -T n8n-postgres psql -U n8n -d n8n < backups/n8n-before-upgrade.sql
docker compose up -d --build
```

Cette procédure concerne uniquement un volume provenant de l'ancienne version,
où la base `app` et le rôle `app` n'existent pas encore. Pour un projet neuf,
utiliser directement les commandes de lancement habituelles.

## Générateur autonome

`create_project.py` utilise uniquement la bibliothèque standard Python et
`pathlib`. Depuis un dossier cible vide :

```sh
python /chemin/vers/create_project.py
```

Il écrit tous les fichiers en UTF-8, crée les dossiers parents, affiche chaque
fichier et les prochaines commandes utiles. Il vérifie toutes les destinations
avant d'écrire et refuse les fichiers existants sans `--force` :

```sh
python /chemin/vers/create_project.py --force
```

`--force` met à jour les fichiers générés et retire l'ancien `backend/app.py`
Flask. Il conserve les autres fichiers, notamment les certificats. Les liens
symboliques ne sont pas suivis lors de la génération.

## Vérification du code

Le workflow `.github/workflows/ci.yml` lance aussi les tests d'intégration avec
PostgreSQL 16 et Redis 7, valide le Compose et construit le frontend à chaque
push ou pull request.

Les tests de génération et de dashboard se lancent avec :

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r tests/requirements.txt
pytest -q tests/test_generator.py tests/test_dashboard.py
cd frontend
npm ci
npm run build
```

Les tests d'intégration backend exigent des instances PostgreSQL et Redis réelles,
avec une base de test vide. Depuis la racine, leur fournir des URLs joignables
depuis l'environnement Python :

```sh
TEST_DATABASE_URL=postgresql://app:app@postgres:5432/app \
TEST_REDIS_URL=redis://redis:6379/1 \
pytest -q tests/test_backend.py
```

Les noms `postgres` et `redis` de cette commande sont résolus lorsque les tests
tournent sur le réseau `app_net`. Ces tests insèrent et suppriment leurs propres
items marqués `integration-test:` ; ils ne suppriment pas les autres données.
''',
    'certs/.gitkeep': r'''''',
    'docker-compose.yml': r'''services:
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
      VIRTUAL_PORT: "80"
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
      VIRTUAL_PORT: "8000"
      VIRTUAL_PROTO: http
      DATABASE_URL: postgresql://app:app@postgres:5432/app
      REDIS_URL: redis://redis:6379/0
      CACHE_TTL_SECONDS: "5"
    expose:
      - "8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=8)"]
      interval: 10s
      timeout: 9s
      retries: 3
      start_period: 15s
    networks:
      - app_net

  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: app
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
    expose:
      - "5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app -d app"]
      interval: 5s
      timeout: 5s
      retries: 20
      start_period: 10s
    networks:
      - app_net

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes"]
    expose:
      - "6379"
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 20
    networks:
      - app_net

  worker:
    build: ./worker
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql://app:app@postgres:5432/app
    depends_on:
      postgres:
        condition: service_healthy
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
      N8N_EDITOR_BASE_URL: https://n8n.apex.tld/
      DB_TYPE: postgresdb
      DB_POSTGRESDB_HOST: n8n-postgres
      DB_POSTGRESDB_PORT: "5432"
      DB_POSTGRESDB_DATABASE: n8n
      DB_POSTGRESDB_USER: n8n
      DB_POSTGRESDB_PASSWORD: n8n
    expose:
      - "5678"
    volumes:
      - n8n_data:/home/node/.n8n
    depends_on:
      n8n-postgres:
        condition: service_healthy
    networks:
      - app_net

  n8n-postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: n8n
      POSTGRES_USER: n8n
      POSTGRES_PASSWORD: n8n
    expose:
      - "5432"
    volumes:
      - n8n_postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U n8n -d n8n"]
      interval: 5s
      timeout: 5s
      retries: 20
      start_period: 10s
    networks:
      - app_net

  prometheus:
    image: prom/prometheus:latest
    restart: unless-stopped
    expose:
      - "9090"
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
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
      - ./grafana/provisioning/datasources:/etc/grafana/provisioning/datasources:ro
    networks:
      - app_net

  ckpit:
    build: ./ckpit
    restart: unless-stopped
    environment:
      VIRTUAL_HOST: ckpit.apex.tld
      VIRTUAL_PORT: "8000"
      VIRTUAL_PROTO: http
    expose:
      - "8000"
    networks:
      - app_net

networks:
  app_net:
    driver: bridge

volumes:
  postgres_data:
  redis_data:
  n8n_data:
  n8n_postgres_data:
  grafana_data:
''',
    'frontend/.dockerignore': r'''node_modules
dist
''',
    'frontend/Dockerfile': r'''FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY index.html .
COPY src ./src
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
''',
    'frontend/package.json': r'''{
  "name": "apex-fullstack-frontend",
  "version": "1.0.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite --host 0.0.0.0",
    "build": "vite build",
    "preview": "vite preview --host 0.0.0.0"
  },
  "devDependencies": {
    "vite": "8.3.4"
  }
}
''',
    'frontend/package-lock.json': r'''{
  "name": "apex-fullstack-frontend",
  "version": "1.0.0",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "": {
      "name": "apex-fullstack-frontend",
      "version": "1.0.0",
      "devDependencies": {
        "vite": "8.3.4"
      }
    },
    "node_modules/@oxc-project/types": {
      "version": "0.153.0",
      "resolved": "https://registry.npmjs.org/@oxc-project/types/-/types-0.153.0.tgz",
      "integrity": "sha512-jSNrRIyvPVLL5E1z/tS5zLAprosGz/FuI1PWwEOz59j3E4TTTzSpqKb/RDGqJhhW0QHYISGXxq582wGx6T11Tw==",
      "dev": true,
      "license": "MIT",
      "funding": {
        "url": "https://github.com/sponsors/oxc-project"
      }
    },
    "node_modules/@rolldown/binding-android-arm-eabi": {
      "version": "1.2.13",
      "resolved": "https://registry.npmjs.org/@rolldown/binding-android-arm-eabi/-/binding-android-arm-eabi-1.2.13.tgz",
      "integrity": "sha512-Vu/NICrgdRvUOeaJZIyB2FP63s6fO/4ud5l8dXmxDx9yDyn+mN0y+gsvsVviSli+T361Aqo0h46EuMoobrwawA==",
      "cpu": [
        "arm"
      ],
      "dev": true,
      "license": "MIT",
      "optional": true,
      "os": [
        "android"
      ],
      "engines": {
        "node": "^20.19.0 || >=22.12.0"
      }
    },
    "node_modules/@rolldown/binding-android-arm64": {
      "version": "1.2.13",
      "resolved": "https://registry.npmjs.org/@rolldown/binding-android-arm64/-/binding-android-arm64-1.2.13.tgz",
      "integrity": "sha512-6LEoIrGy5vvCd2boNnv2soPSUZj/PRvhp8R/t9KlX+mIZxThIE/ZqxrGXxmuNX0FuvQFvLdZr7S4HrYpYBVhEQ==",
      "cpu": [
        "arm64"
      ],
      "dev": true,
      "license": "MIT",
      "optional": true,
      "os": [
        "android"
      ],
      "engines": {
        "node": "^20.19.0 || >=22.12.0"
      }
    },
    "node_modules/@rolldown/binding-darwin-arm64": {
      "version": "1.2.13",
      "resolved": "https://registry.npmjs.org/@rolldown/binding-darwin-arm64/-/binding-darwin-arm64-1.2.13.tgz",
      "integrity": "sha512-CaJSRzlQ6Q+zkDMVA9OkVxBjVoPRCDEE3v0FSJ9kIQBgRjFApA3Si4ijhbNVUji7ZJt+PfO0e46qy4nPInHUPA==",
      "cpu": [
        "arm64"
      ],
      "dev": true,
      "license": "MIT",
      "optional": true,
      "os": [
        "darwin"
      ],
      "engines": {
        "node": "^20.19.0 || >=22.12.0"
      }
    },
    "node_modules/@rolldown/binding-darwin-x64": {
      "version": "1.2.13",
      "resolved": "https://registry.npmjs.org/@rolldown/binding-darwin-x64/-/binding-darwin-x64-1.2.13.tgz",
      "integrity": "sha512-e4r340I5hxSzJkD3YJBxgzq/rJ3wwxwdLLb2FPt1Zpyem20nrQPT11DDEEkazwxaHumH8wpglsp/Ozgj/wT6mw==",
      "cpu": [
        "x64"
      ],
      "dev": true,
      "license": "MIT",
      "optional": true,
      "os": [
        "darwin"
      ],
      "engines": {
        "node": "^20.19.0 || >=22.12.0"
      }
    },
    "node_modules/@rolldown/binding-freebsd-x64": {
      "version": "1.2.13",
      "resolved": "https://registry.npmjs.org/@rolldown/binding-freebsd-x64/-/binding-freebsd-x64-1.2.13.tgz",
      "integrity": "sha512-hoBWDZ4/K/mJKVMbsgUd1jfpA2DiOrJ54mpegObU9TBHBBrtX4FLIXJJQRDPZ/2Dn91GvPob+Z2hwKJzf4E5SQ==",
      "cpu": [
        "x64"
      ],
      "dev": true,
      "license": "MIT",
      "optional": true,
      "os": [
        "freebsd"
      ],
      "engines": {
        "node": "^20.19.0 || >=22.12.0"
      }
    },
    "node_modules/@rolldown/binding-linux-arm-gnueabihf": {
      "version": "1.2.13",
      "resolved": "https://registry.npmjs.org/@rolldown/binding-linux-arm-gnueabihf/-/binding-linux-arm-gnueabihf-1.2.13.tgz",
      "integrity": "sha512-oF9fZu3vTWnTiVAkDFxU9PJLt1nh20K9RRpuHsR7ZnE2L7XookBbDBVLojCVSLGbvtSlxeUNnjAj6DbNsKyzbg==",
      "cpu": [
        "arm"
      ],
      "dev": true,
      "license": "MIT",
      "optional": true,
      "os": [
        "linux"
      ],
      "engines": {
        "node": "^20.19.0 || >=22.12.0"
      }
    },
    "node_modules/@rolldown/binding-linux-arm64-gnu": {
      "version": "1.2.13",
      "resolved": "https://registry.npmjs.org/@rolldown/binding-linux-arm64-gnu/-/binding-linux-arm64-gnu-1.2.13.tgz",
      "integrity": "sha512-DTpb/+jMiqWMPgphn4ksW1zW+XLJ6dJJ9FMvnIk9I291LhCctMPhMYJrP5t9p6C9qa3KXadDdUEZHBaPh3kNxQ==",
      "cpu": [
        "arm64"
      ],
      "dev": true,
      "license": "MIT",
      "optional": true,
      "os": [
        "linux"
      ],
      "engines": {
        "node": "^20.19.0 || >=22.12.0"
      }
    },
    "node_modules/@rolldown/binding-linux-arm64-musl": {
      "version": "1.2.13",
      "resolved": "https://registry.npmjs.org/@rolldown/binding-linux-arm64-musl/-/binding-linux-arm64-musl-1.2.13.tgz",
      "integrity": "sha512-xU+Kvv9N2A3SY35zFh1FWWNjxbadE/BKeqnkXe7vScU4GZsw757BlVmDLITZEq8DPpk6m7uoDYkK7P+x/uU5Ow==",
      "cpu": [
        "arm64"
      ],
      "dev": true,
      "license": "MIT",
      "optional": true,
      "os": [
        "linux"
      ],
      "engines": {
        "node": "^20.19.0 || >=22.12.0"
      }
    },
    "node_modules/@rolldown/binding-linux-ppc64-gnu": {
      "version": "1.2.13",
      "resolved": "https://registry.npmjs.org/@rolldown/binding-linux-ppc64-gnu/-/binding-linux-ppc64-gnu-1.2.13.tgz",
      "integrity": "sha512-XMk/hnhh25oQ756Lclb1Q11h4ll0xC0Esa5w4Vzp+mAE2S3AGJgeM0SonrIgY8fHhjarb6hcoTuwoFG0VVP2ig==",
      "cpu": [
        "ppc64"
      ],
      "dev": true,
      "license": "MIT",
      "optional": true,
      "os": [
        "linux"
      ],
      "engines": {
        "node": "^20.19.0 || >=22.12.0"
      }
    },
    "node_modules/@rolldown/binding-linux-s390x-gnu": {
      "version": "1.2.13",
      "resolved": "https://registry.npmjs.org/@rolldown/binding-linux-s390x-gnu/-/binding-linux-s390x-gnu-1.2.13.tgz",
      "integrity": "sha512-U1BoTHYmlywE2n3HUWjzZ+8EQsKr+OwpxYuDWaubx42tUL8QSL7nOqRz/NVHanibbFP3M1Rxuprz/wNcbMKjqw==",
      "cpu": [
        "s390x"
      ],
      "dev": true,
      "license": "MIT",
      "optional": true,
      "os": [
        "linux"
      ],
      "engines": {
        "node": "^20.19.0 || >=22.12.0"
      }
    },
    "node_modules/@rolldown/binding-linux-x64-gnu": {
      "version": "1.2.13",
      "resolved": "https://registry.npmjs.org/@rolldown/binding-linux-x64-gnu/-/binding-linux-x64-gnu-1.2.13.tgz",
      "integrity": "sha512-J4cm80Pc6wEwV5AZ42o62sIQgDk+Y7R2s646jIwuvO+4V46QB58N4vVuMPWCBaNgursXLpmUWJV//WQAkPvHBQ==",
      "cpu": [
        "x64"
      ],
      "dev": true,
      "license": "MIT",
      "optional": true,
      "os": [
        "linux"
      ],
      "engines": {
        "node": "^20.19.0 || >=22.12.0"
      }
    },
    "node_modules/@rolldown/binding-linux-x64-musl": {
      "version": "1.2.13",
      "resolved": "https://registry.npmjs.org/@rolldown/binding-linux-x64-musl/-/binding-linux-x64-musl-1.2.13.tgz",
      "integrity": "sha512-W0euZS40U4V6JkmEnVCIX9mcsL6cbXQ4X8c2w+MMOIjFg68TXUW+u3RT+2e1t1pv9mg/EP3pDtK3POXdQ7Rxyw==",
      "cpu": [
        "x64"
      ],
      "dev": true,
      "license": "MIT",
      "optional": true,
      "os": [
        "linux"
      ],
      "engines": {
        "node": "^20.19.0 || >=22.12.0"
      }
    },
    "node_modules/@rolldown/binding-openharmony-arm64": {
      "version": "1.2.13",
      "resolved": "https://registry.npmjs.org/@rolldown/binding-openharmony-arm64/-/binding-openharmony-arm64-1.2.13.tgz",
      "integrity": "sha512-do/AF/4n9MRshia3a5aXoy8xM2c6mgfT6D7H/dY6my5z+hfCIR4ab7mk4IRj++nsEQpKy39kd0DQnDq+r692tw==",
      "cpu": [
        "arm64"
      ],
      "dev": true,
      "license": "MIT",
      "optional": true,
      "os": [
        "openharmony"
      ],
      "engines": {
        "node": "^20.19.0 || >=22.12.0"
      }
    },
    "node_modules/@rolldown/binding-win32-arm64-msvc": {
      "version": "1.2.13",
      "resolved": "https://registry.npmjs.org/@rolldown/binding-win32-arm64-msvc/-/binding-win32-arm64-msvc-1.2.13.tgz",
      "integrity": "sha512-6FtUf2cJZobutJdxnGYX8z0vtbgqU0b2v8vfBhkXb9wp2MppMFtkunE+IaOntyi01rkIQoPW2GHABtfzsC0BRQ==",
      "cpu": [
        "arm64"
      ],
      "dev": true,
      "license": "MIT",
      "optional": true,
      "os": [
        "win32"
      ],
      "engines": {
        "node": "^20.19.0 || >=22.12.0"
      }
    },
    "node_modules/@rolldown/binding-win32-x64-msvc": {
      "version": "1.2.13",
      "resolved": "https://registry.npmjs.org/@rolldown/binding-win32-x64-msvc/-/binding-win32-x64-msvc-1.2.13.tgz",
      "integrity": "sha512-UKhFaZTeTcbKrb3iU82m17jVcPEAoqDkCN5MBqgPyZSwp/cQmib21crTS0DdH7T1B7wZRWEI89YQAOJMet5yhg==",
      "cpu": [
        "x64"
      ],
      "dev": true,
      "license": "MIT",
      "optional": true,
      "os": [
        "win32"
      ],
      "engines": {
        "node": "^20.19.0 || >=22.12.0"
      }
    },
    "node_modules/@rolldown/pluginutils": {
      "version": "1.0.1",
      "resolved": "https://registry.npmjs.org/@rolldown/pluginutils/-/pluginutils-1.0.1.tgz",
      "integrity": "sha512-2j9bGt5Jh8hj+vPtgzPtl72j0yRxHAyumoo6TNfAjsLB04UtpSvPbPcDcBMxz7n+9CYB0c1GxQFxYRg2jimqGw==",
      "dev": true,
      "license": "MIT"
    },
    "node_modules/detect-libc": {
      "version": "2.1.2",
      "resolved": "https://registry.npmjs.org/detect-libc/-/detect-libc-2.1.2.tgz",
      "integrity": "sha512-Btj2BOOO83o3WyH59e8MgXsxEQVcarkUOpEYrubB0urwnN10yQ364rsiByU11nZlqWYZm05i/of7io4mzihBtQ==",
      "dev": true,
      "license": "Apache-2.0",
      "engines": {
        "node": ">=8"
      }
    },
    "node_modules/fdir": {
      "version": "6.5.0",
      "resolved": "https://registry.npmjs.org/fdir/-/fdir-6.5.0.tgz",
      "integrity": "sha512-tIbYtZbucOs0BRGqPJkshJUYdL+SDH7dVM8gjy+ERp3WAUjLEFJE+02kanyHtwjWOnwrKYBiwAmM0p4kLJAnXg==",
      "dev": true,
      "license": "MIT",
      "engines": {
        "node": ">=12.0.0"
      },
      "peerDependencies": {
        "picomatch": "^3 || ^4"
      },
      "peerDependenciesMeta": {
        "picomatch": {
          "optional": true
        }
      }
    },
    "node_modules/fsevents": {
      "version": "2.3.3",
      "resolved": "https://registry.npmjs.org/fsevents/-/fsevents-2.3.3.tgz",
      "integrity": "sha512-5xoDfX+fL7faATnagmWPpbFtwh/R77WmMMqqHGS65C3vvB0YHrgF+B1YmZ3441tMj5n63k0212XNoJwzlhffQw==",
      "dev": true,
      "hasInstallScript": true,
      "license": "MIT",
      "optional": true,
      "os": [
        "darwin"
      ],
      "engines": {
        "node": "^8.16.0 || ^10.6.0 || >=11.0.0"
      }
    },
    "node_modules/lightningcss": {
      "version": "1.33.0",
      "resolved": "https://registry.npmjs.org/lightningcss/-/lightningcss-1.33.0.tgz",
      "integrity": "sha512-WkUDrojuJs0xkgGf2udWxa3yGBRxPtxUkB79i6aCZLRgc7PM8fZe9TosfPDcvEpQZbuFASnHYmRLBLUbmLOIIA==",
      "dev": true,
      "license": "MPL-2.0",
      "dependencies": {
        "detect-libc": "^2.0.3"
      },
      "engines": {
        "node": ">= 12.0.0"
      },
      "funding": {
        "type": "opencollective",
        "url": "https://opencollective.com/parcel"
      },
      "optionalDependencies": {
        "lightningcss-android-arm64": "1.33.0",
        "lightningcss-darwin-arm64": "1.33.0",
        "lightningcss-darwin-x64": "1.33.0",
        "lightningcss-freebsd-x64": "1.33.0",
        "lightningcss-linux-arm-gnueabihf": "1.33.0",
        "lightningcss-linux-arm64-gnu": "1.33.0",
        "lightningcss-linux-arm64-musl": "1.33.0",
        "lightningcss-linux-x64-gnu": "1.33.0",
        "lightningcss-linux-x64-musl": "1.33.0",
        "lightningcss-win32-arm64-msvc": "1.33.0",
        "lightningcss-win32-x64-msvc": "1.33.0"
      }
    },
    "node_modules/lightningcss-android-arm64": {
      "version": "1.33.0",
      "resolved": "https://registry.npmjs.org/lightningcss-android-arm64/-/lightningcss-android-arm64-1.33.0.tgz",
      "integrity": "sha512-gEpRTalKdosp4Bb8qWtc2iOgE5SeIHlpS1up9bFq2wAyYhl1UdTObYiHe98zEM9SQvSoqQZ1IQD0JNpg3Ml5pg==",
      "cpu": [
        "arm64"
      ],
      "dev": true,
      "license": "MPL-2.0",
      "optional": true,
      "os": [
        "android"
      ],
      "engines": {
        "node": ">= 12.0.0"
      },
      "funding": {
        "type": "opencollective",
        "url": "https://opencollective.com/parcel"
      }
    },
    "node_modules/lightningcss-darwin-arm64": {
      "version": "1.33.0",
      "resolved": "https://registry.npmjs.org/lightningcss-darwin-arm64/-/lightningcss-darwin-arm64-1.33.0.tgz",
      "integrity": "sha512-Sciaz8eenNTKn9b3t7+xr0ipTp9YxKQY4npwQ3mrRuL0BAVHBLyZxofhaKBAVtzmtRZ/zTyo0/to4B1uWG/Djg==",
      "cpu": [
        "arm64"
      ],
      "dev": true,
      "license": "MPL-2.0",
      "optional": true,
      "os": [
        "darwin"
      ],
      "engines": {
        "node": ">= 12.0.0"
      },
      "funding": {
        "type": "opencollective",
        "url": "https://opencollective.com/parcel"
      }
    },
    "node_modules/lightningcss-darwin-x64": {
      "version": "1.33.0",
      "resolved": "https://registry.npmjs.org/lightningcss-darwin-x64/-/lightningcss-darwin-x64-1.33.0.tgz",
      "integrity": "sha512-Z5UPAxzrjlWNNyGy6i65cJzzvgJ5D3T6wMvs+gWpY9d7qRhANrxqAp6LhxIgZhWEw18RfJTGcRxjuLIBr+m8XQ==",
      "cpu": [
        "x64"
      ],
      "dev": true,
      "license": "MPL-2.0",
      "optional": true,
      "os": [
        "darwin"
      ],
      "engines": {
        "node": ">= 12.0.0"
      },
      "funding": {
        "type": "opencollective",
        "url": "https://opencollective.com/parcel"
      }
    },
    "node_modules/lightningcss-freebsd-x64": {
      "version": "1.33.0",
      "resolved": "https://registry.npmjs.org/lightningcss-freebsd-x64/-/lightningcss-freebsd-x64-1.33.0.tgz",
      "integrity": "sha512-QQM/Ti/hQajJwCY+RiWuCZ9sdtI/XQk7nDK5vC8kkdwixezOlDgvDx7+RT+QjK6FcFT4MpsuoBnHIo/O3StRRg==",
      "cpu": [
        "x64"
      ],
      "dev": true,
      "license": "MPL-2.0",
      "optional": true,
      "os": [
        "freebsd"
      ],
      "engines": {
        "node": ">= 12.0.0"
      },
      "funding": {
        "type": "opencollective",
        "url": "https://opencollective.com/parcel"
      }
    },
    "node_modules/lightningcss-linux-arm-gnueabihf": {
      "version": "1.33.0",
      "resolved": "https://registry.npmjs.org/lightningcss-linux-arm-gnueabihf/-/lightningcss-linux-arm-gnueabihf-1.33.0.tgz",
      "integrity": "sha512-N7FVBe6iS24MlM6R/4RBTxGhQheZGs7tiQ9U32UtF75NzP5Q7xWPRqLBCKxlRQRk3rY1jCIPLzx7WzOhuUIRLQ==",
      "cpu": [
        "arm"
      ],
      "dev": true,
      "license": "MPL-2.0",
      "optional": true,
      "os": [
        "linux"
      ],
      "engines": {
        "node": ">= 12.0.0"
      },
      "funding": {
        "type": "opencollective",
        "url": "https://opencollective.com/parcel"
      }
    },
    "node_modules/lightningcss-linux-arm64-gnu": {
      "version": "1.33.0",
      "resolved": "https://registry.npmjs.org/lightningcss-linux-arm64-gnu/-/lightningcss-linux-arm64-gnu-1.33.0.tgz",
      "integrity": "sha512-j2v/itmy4HlNxlc6voKXYgBqNi0Ng2LShg4z7GufpEgs05P+2suBVyi9I6YHq5uoVFx9ETin3eCEhLVyXGQnKg==",
      "cpu": [
        "arm64"
      ],
      "dev": true,
      "license": "MPL-2.0",
      "optional": true,
      "os": [
        "linux"
      ],
      "engines": {
        "node": ">= 12.0.0"
      },
      "funding": {
        "type": "opencollective",
        "url": "https://opencollective.com/parcel"
      }
    },
    "node_modules/lightningcss-linux-arm64-musl": {
      "version": "1.33.0",
      "resolved": "https://registry.npmjs.org/lightningcss-linux-arm64-musl/-/lightningcss-linux-arm64-musl-1.33.0.tgz",
      "integrity": "sha512-yiO5ROMuYQgXbC60yjZU5CYSFZGKXL0HFATXt9mHJn1+zW55oCtMI9NfcVhYLMFDL7gV7oBPon/EmMMGg2OvtQ==",
      "cpu": [
        "arm64"
      ],
      "dev": true,
      "license": "MPL-2.0",
      "optional": true,
      "os": [
        "linux"
      ],
      "engines": {
        "node": ">= 12.0.0"
      },
      "funding": {
        "type": "opencollective",
        "url": "https://opencollective.com/parcel"
      }
    },
    "node_modules/lightningcss-linux-x64-gnu": {
      "version": "1.33.0",
      "resolved": "https://registry.npmjs.org/lightningcss-linux-x64-gnu/-/lightningcss-linux-x64-gnu-1.33.0.tgz",
      "integrity": "sha512-ar+Ju7LmcN0Jo4FpL4hpFybwNG9/3A/Br5KW2n2jyODg3MEZXaDYADdemoNS+BDNfMgKvylJLj4S5tyRActuAg==",
      "cpu": [
        "x64"
      ],
      "dev": true,
      "license": "MPL-2.0",
      "optional": true,
      "os": [
        "linux"
      ],
      "engines": {
        "node": ">= 12.0.0"
      },
      "funding": {
        "type": "opencollective",
        "url": "https://opencollective.com/parcel"
      }
    },
    "node_modules/lightningcss-linux-x64-musl": {
      "version": "1.33.0",
      "resolved": "https://registry.npmjs.org/lightningcss-linux-x64-musl/-/lightningcss-linux-x64-musl-1.33.0.tgz",
      "integrity": "sha512-RYiYbkokw0trfKqqzfF55lginwEPrD3OJDfTuJzFs1MK6iFnDenaz1fqLLtX4ITG3OktJQXOeTaw1awrBAlZPw==",
      "cpu": [
        "x64"
      ],
      "dev": true,
      "license": "MPL-2.0",
      "optional": true,
      "os": [
        "linux"
      ],
      "engines": {
        "node": ">= 12.0.0"
      },
      "funding": {
        "type": "opencollective",
        "url": "https://opencollective.com/parcel"
      }
    },
    "node_modules/lightningcss-win32-arm64-msvc": {
      "version": "1.33.0",
      "resolved": "https://registry.npmjs.org/lightningcss-win32-arm64-msvc/-/lightningcss-win32-arm64-msvc-1.33.0.tgz",
      "integrity": "sha512-1K+MPfLSFVpphzpdbfkhlWk6wBrTObBzS2T6db10PNOZgR9GoVsAWzwNyuhUYYbTp23j+4RrncfujZ4uAzXvwA==",
      "cpu": [
        "arm64"
      ],
      "dev": true,
      "license": "MPL-2.0",
      "optional": true,
      "os": [
        "win32"
      ],
      "engines": {
        "node": ">= 12.0.0"
      },
      "funding": {
        "type": "opencollective",
        "url": "https://opencollective.com/parcel"
      }
    },
    "node_modules/lightningcss-win32-x64-msvc": {
      "version": "1.33.0",
      "resolved": "https://registry.npmjs.org/lightningcss-win32-x64-msvc/-/lightningcss-win32-x64-msvc-1.33.0.tgz",
      "integrity": "sha512-OlEICDx/Xl0FqSp4bry8zFnCvGpig3Gl4gCquvYwHuqJKEC1+n9NgDniFvqHGmMv1ZkqDJrDqKKSykTDX+ehuA==",
      "cpu": [
        "x64"
      ],
      "dev": true,
      "license": "MPL-2.0",
      "optional": true,
      "os": [
        "win32"
      ],
      "engines": {
        "node": ">= 12.0.0"
      },
      "funding": {
        "type": "opencollective",
        "url": "https://opencollective.com/parcel"
      }
    },
    "node_modules/nanoid": {
      "version": "3.3.20",
      "resolved": "https://registry.npmjs.org/nanoid/-/nanoid-3.3.20.tgz",
      "integrity": "sha512-uKdg2G3GNCKQn9byYOpxbGqrT2fGO5KRt5J/8b3pok8rT6qxGWF6hxMyJiEYtAf+FVyYuD9hRaDqX5uPFYJ4ZQ==",
      "dev": true,
      "funding": [
        {
          "type": "github",
          "url": "https://github.com/sponsors/ai"
        }
      ],
      "license": "MIT",
      "bin": {
        "nanoid": "bin/nanoid.cjs"
      },
      "engines": {
        "node": "^10 || ^12 || ^13.7 || ^14 || >=15.0.1"
      }
    },
    "node_modules/picocolors": {
      "version": "1.1.1",
      "resolved": "https://registry.npmjs.org/picocolors/-/picocolors-1.1.1.tgz",
      "integrity": "sha512-xceH2snhtb5M9liqDsmEw56le376mTZkEX/jEb/RxNFyegNul7eNslCXP9FDj/Lcu0X8KEyMceP2ntpaHrDEVA==",
      "dev": true,
      "license": "ISC"
    },
    "node_modules/picomatch": {
      "version": "4.0.7",
      "resolved": "https://registry.npmjs.org/picomatch/-/picomatch-4.0.7.tgz",
      "integrity": "sha512-qcJu88Q2IWqJsDD529JKMdwGm/dvInW4HvQnRwiH9JtihJvzGOscDtHE3x1pBKeUOTysQ8kVmLnJ2kJu7yhcGA==",
      "dev": true,
      "license": "MIT",
      "engines": {
        "node": ">=12"
      },
      "funding": {
        "url": "https://github.com/sponsors/jonschlinkert"
      }
    },
    "node_modules/postcss": {
      "version": "8.5.29",
      "resolved": "https://registry.npmjs.org/postcss/-/postcss-8.5.29.tgz",
      "integrity": "sha512-49cGhUbXj8Qenv0iTMxA1cFBzxXoctpC9Ujd77t1WcbJIr6nF/eI7g/8MgxrYldFRuAXvja7xQRwavoW7kgrxQ==",
      "dev": true,
      "funding": [
        {
          "type": "opencollective",
          "url": "https://opencollective.com/postcss/"
        },
        {
          "type": "tidelift",
          "url": "https://tidelift.com/funding/github/npm/postcss"
        },
        {
          "type": "github",
          "url": "https://github.com/sponsors/ai"
        }
      ],
      "license": "MIT",
      "dependencies": {
        "nanoid": "^3.3.19",
        "picocolors": "^1.1.1",
        "source-map-js": "^1.2.2"
      },
      "engines": {
        "node": "^10 || ^12 || >=14"
      }
    },
    "node_modules/rolldown": {
      "version": "1.2.13",
      "resolved": "https://registry.npmjs.org/rolldown/-/rolldown-1.2.13.tgz",
      "integrity": "sha512-/5apOkwWxtACcQtZ5WlrAsKS4JwXVThQPJZ3RqIruRY9UG9M8e0VlXN068xum/JRKA4aIUoJ13ghe/kGCvidiQ==",
      "dev": true,
      "license": "MIT",
      "dependencies": {
        "@oxc-project/types": "=0.153.0",
        "@rolldown/pluginutils": "^1.0.0"
      },
      "bin": {
        "rolldown": "bin/cli.mjs"
      },
      "engines": {
        "node": "^20.19.0 || >=22.12.0"
      },
      "optionalDependencies": {
        "@rolldown/binding-android-arm-eabi": "1.2.13",
        "@rolldown/binding-android-arm64": "1.2.13",
        "@rolldown/binding-darwin-arm64": "1.2.13",
        "@rolldown/binding-darwin-x64": "1.2.13",
        "@rolldown/binding-freebsd-x64": "1.2.13",
        "@rolldown/binding-linux-arm-gnueabihf": "1.2.13",
        "@rolldown/binding-linux-arm64-gnu": "1.2.13",
        "@rolldown/binding-linux-arm64-musl": "1.2.13",
        "@rolldown/binding-linux-ppc64-gnu": "1.2.13",
        "@rolldown/binding-linux-s390x-gnu": "1.2.13",
        "@rolldown/binding-linux-x64-gnu": "1.2.13",
        "@rolldown/binding-linux-x64-musl": "1.2.13",
        "@rolldown/binding-openharmony-arm64": "1.2.13",
        "@rolldown/binding-win32-arm64-msvc": "1.2.13",
        "@rolldown/binding-win32-x64-msvc": "1.2.13"
      }
    },
    "node_modules/source-map-js": {
      "version": "1.2.2",
      "resolved": "https://registry.npmjs.org/source-map-js/-/source-map-js-1.2.2.tgz",
      "integrity": "sha512-KGj/8Y43x35aZVDtt+J4mK1hoLGHULMYfSkODJNQjNDC3oW1PqPoxMwo0pLUsWM/UEGzON/NxeHywEfNXNP3Vw==",
      "dev": true,
      "license": "BSD-3-Clause",
      "engines": {
        "node": ">=0.10.0"
      }
    },
    "node_modules/tinyglobby": {
      "version": "0.2.17",
      "resolved": "https://registry.npmjs.org/tinyglobby/-/tinyglobby-0.2.17.tgz",
      "integrity": "sha512-wXR/dYpcqKmfWpEdZjiKJOwCNFndD0DMnrW/cYjVGttEkBfVgcLFHoNrlj47mjOVic9yyNu65alsgF4NQyTa2g==",
      "dev": true,
      "license": "MIT",
      "dependencies": {
        "fdir": "^6.5.0",
        "picomatch": "^4.0.4"
      },
      "engines": {
        "node": ">=12.0.0"
      },
      "funding": {
        "url": "https://github.com/sponsors/SuperchupuDev"
      }
    },
    "node_modules/vite": {
      "version": "8.3.4",
      "resolved": "https://registry.npmjs.org/vite/-/vite-8.3.4.tgz",
      "integrity": "sha512-IftLE8phJhdNRIGNEV4JCCvfNScoZQVfcXaN8TQropWGJtOHcu+iAB9pHcuGvmEb/kddw0LBe/jS8ztdC6xdiQ==",
      "dev": true,
      "license": "MIT",
      "dependencies": {
        "lightningcss": "^1.33.0",
        "picomatch": "^4.0.7",
        "postcss": "^8.5.29",
        "rolldown": "~1.2.12",
        "tinyglobby": "^0.2.17"
      },
      "bin": {
        "vite": "bin/vite.js"
      },
      "engines": {
        "node": "^20.19.0 || >=22.12.0"
      },
      "funding": {
        "url": "https://github.com/vitejs/vite?sponsor=1"
      },
      "optionalDependencies": {
        "fsevents": "~2.3.3"
      },
      "peerDependencies": {
        "@types/node": "^20.19.0 || >=22.12.0",
        "@vitejs/devtools": "^0.7.1",
        "esbuild": "^0.27.0 || ^0.28.0",
        "jiti": ">=1.21.0",
        "less": "^4.0.0",
        "sass": "^1.70.0",
        "sass-embedded": "^1.70.0",
        "stylus": ">=0.54.8",
        "sugarss": "^5.0.0",
        "terser": "^5.16.0",
        "tsx": "^4.8.1",
        "yaml": "^2.4.2"
      },
      "peerDependenciesMeta": {
        "@types/node": {
          "optional": true
        },
        "@vitejs/devtools": {
          "optional": true
        },
        "esbuild": {
          "optional": true
        },
        "jiti": {
          "optional": true
        },
        "less": {
          "optional": true
        },
        "sass": {
          "optional": true
        },
        "sass-embedded": {
          "optional": true
        },
        "stylus": {
          "optional": true
        },
        "sugarss": {
          "optional": true
        },
        "terser": {
          "optional": true
        },
        "tsx": {
          "optional": true
        },
        "yaml": {
          "optional": true
        }
      }
    }
  }
}
''',
    'frontend/index.html': r'''<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="Application Apex hébergée sur une seule VM.">
  <title>Apex Fullstack</title>
</head>
<body>
  <main>
    <header>
      <p class="eyebrow">SELF-HOSTED · UNE SEULE MACHINE</p>
      <h1>Apex Fullstack</h1>
      <p class="domain">frontend.apex.tld</p>
      <nav aria-label="Services de la stack">
        <a href="https://backend.apex.tld/docs">Backend</a>
        <a href="https://grafana.apex.tld">Grafana</a>
        <a href="https://n8n.apex.tld">n8n</a>
        <a href="https://ckpit.apex.tld">ckpit</a>
      </nav>
    </header>
    <section aria-labelledby="health-title">
      <div class="section-heading">
        <h2 id="health-title">État du backend</h2>
        <button id="refresh-health" type="button">Vérifier</button>
      </div>
      <p><a href="https://backend.apex.tld/health">https://backend.apex.tld/health</a></p>
      <pre id="health-result" aria-live="polite">Vérification en cours…</pre>
    </section>
    <section aria-labelledby="items-title">
      <div class="section-heading">
        <h2 id="items-title">Messages de la stack</h2>
        <button id="refresh-items" type="button">Actualiser</button>
      </div>
      <p>Messages enregistrés dans PostgreSQL. Le worker ajoute un message toutes les 30 secondes.</p>
      <form id="item-form">
        <label for="message">Nouveau message</label>
        <textarea id="message" name="message" maxlength="500" rows="3" required></textarea>
        <button id="submit-item" type="submit">Créer un item</button>
      </form>
      <p id="item-feedback" role="status" aria-live="polite"></p>
      <p id="items-status" aria-live="polite"></p>
      <ul id="items" class="items"></ul>
    </section>
    <footer>Les données et les interfaces sont hébergées dans cette stack Docker.</footer>
  </main>
  <script type="module" src="/src/main.js"></script>
</body>
</html>
''',
    'frontend/src/main.js': r'''import './style.css';

const API_URL = 'https://backend.apex.tld';
const healthResult = document.querySelector('#health-result');
const itemsList = document.querySelector('#items');
const itemsStatus = document.querySelector('#items-status');
const feedback = document.querySelector('#item-feedback');
const form = document.querySelector('#item-form');
const messageInput = document.querySelector('#message');
const submitButton = document.querySelector('#submit-item');
let healthPending = false;
let itemsPending = false;

async function api(path, options = {}) {
  const response = await fetch(API_URL + path, {
    ...options,
    signal: AbortSignal.timeout(10000),
  });
  if (!response.ok) {
    throw new Error('HTTP ' + response.status + ' : ' + await response.text());
  }
  return response.json();
}

async function refreshHealth() {
  if (healthPending) return;
  healthPending = true;
  try {
    const result = await api('/health');
    healthResult.textContent = JSON.stringify(result, null, 2);
    healthResult.className = 'success';
  } catch (error) {
    healthResult.textContent = 'Échec de la vérification : ' + error.message;
    healthResult.className = 'error';
  } finally {
    healthPending = false;
  }
}

async function refreshItems() {
  if (itemsPending) return;
  itemsPending = true;
  itemsStatus.textContent = 'Chargement des items…';
  itemsStatus.className = '';
  try {
    const items = await api('/items');
    itemsList.replaceChildren();
    for (const item of items) {
      const row = document.createElement('li');
      const message = document.createElement('p');
      const details = document.createElement('small');
      message.textContent = item.message;
      details.textContent = '#' + item.id + ' · ' + new Date(item.created_at).toLocaleString();
      row.append(message, details);
      itemsList.append(row);
    }
    itemsStatus.textContent = items.length === 0
      ? 'Aucun item enregistré.'
      : items.length + ' derniers items · actualisation ' + new Date().toLocaleTimeString();
  } catch (error) {
    itemsList.replaceChildren();
    itemsStatus.textContent = 'Impossible de charger les items : ' + error.message;
    itemsStatus.className = 'error';
  } finally {
    itemsPending = false;
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message) {
    feedback.textContent = 'Saisir un message non vide.';
    feedback.className = 'error';
    return;
  }
  submitButton.disabled = true;
  try {
    const item = await api('/items', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message }),
    });
    feedback.textContent = 'Item #' + item.id + ' enregistré.';
    feedback.className = 'success';
    form.reset();
    await refreshItems();
  } catch (error) {
    feedback.textContent = 'Création impossible : ' + error.message;
    feedback.className = 'error';
  } finally {
    submitButton.disabled = false;
  }
});

document.querySelector('#refresh-health').addEventListener('click', refreshHealth);
document.querySelector('#refresh-items').addEventListener('click', refreshItems);
void Promise.all([refreshHealth(), refreshItems()]);
setInterval(() => { void Promise.all([refreshHealth(), refreshItems()]); }, 15000);
''',
    'frontend/src/style.css': r''':root { font-family: Inter, ui-sans-serif, system-ui, sans-serif; color: #e8eef7; background: #0b1220; line-height: 1.6; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-width: 960px; margin: 0 auto; padding: 48px 24px; }
header { margin-bottom: 32px; }
.eyebrow { color: #68d6cb; font-size: 12px; font-weight: 700; letter-spacing: 0.14em; }
h1 { font-size: clamp(36px, 6vw, 60px); line-height: 1.1; margin: 16px 0; letter-spacing: -0.04em; }
h2 { font-size: 22px; margin: 0; }
.domain { color: #a7b9d1; }
nav { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 24px; }
a { color: #83dad2; overflow-wrap: anywhere; }
nav a { padding: 8px 16px; border: 1px solid #30445f; border-radius: 8px; text-decoration: none; }
section { margin: 20px 0; padding: 24px; border: 1px solid #263751; border-radius: 16px; background: #121d30; }
.section-heading { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px; }
button { border: 0; border-radius: 8px; background: #80e0d2; color: #08201c; padding: 10px 16px; font: inherit; font-weight: 650; cursor: pointer; }
button:hover { background: #a0eee3; }
button:disabled { opacity: 0.5; cursor: wait; }
button:focus-visible, a:focus-visible, textarea:focus-visible { outline: 3px solid #c0f5ed; outline-offset: 4px; }
pre { padding: 16px; border-radius: 8px; background: #0b1424; white-space: pre-wrap; overflow-wrap: anywhere; }
form { display: grid; gap: 12px; }
form button { justify-self: start; }
textarea { width: 100%; resize: vertical; border: 1px solid #405775; border-radius: 8px; padding: 12px; background: #0b1424; color: #e8eef7; font: inherit; }
.items { list-style: none; padding: 0; }
.items li { border-top: 1px solid #30445f; padding: 14px 0; }
.items p { margin: 0 0 6px; white-space: pre-wrap; overflow-wrap: anywhere; }
small, footer { color: #a7b9d1; }
.success { color: #a2ecd0; }
.error { color: #ffb5ba; }
footer { margin-top: 32px; font-size: 13px; }
@media (max-width: 600px) { main { padding: 28px 16px; } section { padding: 18px; } }
''',
    'frontend/nginx.conf': r'''server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri $uri/ =404;
    }
}
''',
    'backend/Dockerfile': r'''FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
''',
    'backend/requirements.txt': r'''fastapi==0.143.0
uvicorn==0.54.0
psycopg[binary]==3.3.6
redis==7.4.1
prometheus-client==0.26.0
''',
    'backend/app/main.py': r'''import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime

import psycopg
import redis
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger("uvicorn.error")
DATABASE_URL = os.environ["DATABASE_URL"]
CACHE_TTL_SECONDS = int(os.environ["CACHE_TTL_SECONDS"])
if CACHE_TTL_SECONDS <= 0:
    raise ValueError("CACHE_TTL_SECONDS must be positive")

cache = redis.Redis.from_url(
    os.environ["REDIS_URL"],
    decode_responses=True,
    socket_connect_timeout=3,
    socket_timeout=3,
)
CACHE_KEY = "apex:items:latest"
SCHEMA_LOCK = 861157001
CREATE_ITEMS_TABLE = """
CREATE TABLE IF NOT EXISTS items (
    id BIGSERIAL PRIMARY KEY,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""

HTTP_REQUESTS = Counter(
    "backend_http_requests_total", "HTTP requests", ["method", "path", "status"]
)
HTTP_DURATION = Histogram(
    "backend_http_request_duration_seconds", "HTTP duration", ["method", "path"]
)
ITEMS_CREATED = Counter("backend_items_created_total", "Items created by the API")
CACHE_HITS = Counter("backend_cache_hits_total", "Item cache hits")
CACHE_MISSES = Counter("backend_cache_misses_total", "Item cache misses")
DATABASE_UP = Gauge("backend_database_up", "PostgreSQL connectivity: 1 or 0")
REDIS_UP = Gauge("backend_redis_up", "Redis connectivity: 1 or 0")


def connect_database():
    return psycopg.connect(
        DATABASE_URL,
        connect_timeout=3,
        options="-c statement_timeout=5000",
        row_factory=dict_row,
    )


def initialize_database():
    with connect_database() as connection:
        # Le backend et le worker peuvent démarrer simultanément.
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK,))
        connection.execute(CREATE_ITEMS_TABLE)


@asynccontextmanager
async def lifespan(application):
    initialize_database()
    cache.ping()
    try:
        yield
    finally:
        cache.close()


app = FastAPI(title="Apex Backend", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://frontend.apex.tld"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class ItemCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    message: str = Field(min_length=1, max_length=500)


class Item(BaseModel):
    id: int
    message: str
    created_at: datetime


@app.middleware("http")
async def collect_metrics(request: Request, call_next):
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        path = route.path if route is not None else "unmatched"
        if path != "/metrics":
            method = request.method if request.method in {"GET", "POST", "OPTIONS"} else "OTHER"
            HTTP_REQUESTS.labels(method, path, str(status)).inc()
            HTTP_DURATION.labels(method, path).observe(time.perf_counter() - started)


@app.exception_handler(psycopg.Error)
async def database_error(request: Request, exception: psycopg.Error):
    logger.error("PostgreSQL request failed: %s", exception)
    return JSONResponse(status_code=503, content={"detail": "PostgreSQL unavailable"})


@app.exception_handler(redis.exceptions.RedisError)
async def redis_error(request: Request, exception: redis.exceptions.RedisError):
    logger.error("Redis request failed: %s", exception)
    return JSONResponse(status_code=503, content={"detail": "Redis unavailable"})


@app.get("/")
def index():
    return {
        "service": "backend",
        "url": "https://backend.apex.tld",
        "status": "ok",
        "database": "postgres",
        "cache": "redis",
    }


def dependency_status():
    database_status = "ok"
    redis_status = "ok"
    try:
        with connect_database() as connection:
            connection.execute("SELECT 1").fetchone()
    except psycopg.Error:
        database_status = "error"
    try:
        if not cache.ping():
            redis_status = "error"
    except redis.exceptions.RedisError:
        redis_status = "error"
    DATABASE_UP.set(int(database_status == "ok"))
    REDIS_UP.set(int(redis_status == "ok"))
    healthy = database_status == redis_status == "ok"
    return {
        "status": "healthy" if healthy else "unhealthy",
        "database": database_status,
        "redis": redis_status,
    }


@app.get("/health")
def health():
    result = dependency_status()
    return JSONResponse(
        status_code=200 if result["status"] == "healthy" else 503,
        content=result,
    )


@app.get("/items", response_model=list[Item])
def list_items():
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        CACHE_HITS.inc()
        return json.loads(cached)
    CACHE_MISSES.inc()
    with connect_database() as connection:
        rows = connection.execute(
            "SELECT id, message, created_at FROM items ORDER BY id DESC LIMIT 20"
        ).fetchall()
    items = [Item.model_validate(row).model_dump(mode="json") for row in rows]
    cache.setex(CACHE_KEY, CACHE_TTL_SECONDS, json.dumps(items))
    return items


@app.post("/items", response_model=Item, status_code=201)
def create_item(item: ItemCreate):
    with connect_database() as connection:
        row = connection.execute(
            "INSERT INTO items (message) VALUES (%s) RETURNING id, message, created_at",
            (item.message,),
        ).fetchone()
        # Une panne Redis fait échouer la transaction avant son commit.
        cache.delete(CACHE_KEY)
    ITEMS_CREATED.inc()
    return row


@app.get("/metrics", include_in_schema=False)
def metrics():
    dependency_status()
    return Response(content=generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})
''',
    'worker/Dockerfile': r'''FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY worker.py .
USER 10001:10001
CMD ["python", "worker.py"]
''',
    'worker/requirements.txt': r'''psycopg[binary]==3.3.6
''',
    'worker/worker.py': r'''import logging
import os
import signal
import sys
from datetime import datetime, timezone
from threading import Event

import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]
SCHEMA_LOCK = 861157001
CREATE_ITEMS_TABLE = """
CREATE TABLE IF NOT EXISTS items (
    id BIGSERIAL PRIMARY KEY,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""
logger = logging.getLogger("worker")


def connect_database():
    return psycopg.connect(
        DATABASE_URL, connect_timeout=3, options="-c statement_timeout=5000"
    )


def initialize_database():
    with connect_database() as connection:
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (SCHEMA_LOCK,))
        connection.execute(CREATE_ITEMS_TABLE)


def insert_tick():
    message = f"Worker tick — {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    with connect_database() as connection:
        row = connection.execute(
            "INSERT INTO items (message) VALUES (%s) RETURNING id", (message,)
        ).fetchone()
    logger.info("Inserted item %s: %s", row[0], message)
    return row[0]


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(message)s")
    stopped = Event()

    def stop(signum, frame):
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        initialize_database()
        logger.info("Worker started; insert interval: 30 seconds")
        while not stopped.is_set():
            insert_tick()
            stopped.wait(30)
    except psycopg.Error:
        logger.exception("PostgreSQL unavailable; worker stopped")
        return 1
    logger.info("Worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
''',
    'ckpit/Dockerfile': r'''FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
''',
    'ckpit/requirements.txt': r'''fastapi==0.143.0
uvicorn==0.54.0
httpx==0.28.1
Jinja2==3.1.6
''',
    'ckpit/app/main.py': r'''import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

APP_DIRECTORY = Path(__file__).parent
PUBLIC_URLS = [
    {"name": "Frontend", "url": "https://frontend.apex.tld"},
    {"name": "Backend", "url": "https://backend.apex.tld"},
    {"name": "Grafana", "url": "https://grafana.apex.tld"},
    {"name": "n8n", "url": "https://n8n.apex.tld"},
    {"name": "ckpit", "url": "https://ckpit.apex.tld"},
]


@asynccontextmanager
async def lifespan(application):
    async with httpx.AsyncClient(timeout=3, follow_redirects=False, trust_env=False) as client:
        application.state.http = client
        yield


app = FastAPI(title="Apex Stack Cockpit", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=APP_DIRECTORY / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIRECTORY / "templates")


async def probe(client, label, url, json_body=False):
    result = {"label": label, "url": url, "state": "failed", "data": None}
    try:
        response = await client.get(url)
    except httpx.RequestError as exception:
        result["detail"] = f"Requête échouée : {type(exception).__name__}"
        return result
    result["state"] = "healthy" if 200 <= response.status_code < 400 else "failed"
    result["detail"] = f"HTTP {response.status_code}"
    if json_body:
        try:
            data = response.json()
        except ValueError:
            result.update(state="failed", detail="Réponse JSON invalide")
            return result
        if not isinstance(data, dict):
            result.update(state="failed", detail="Objet JSON attendu")
            return result
        result["data"] = data
    return result


def backend_component(backend, field, label):
    data = backend["data"]
    if data is None or field not in data:
        return {
            "label": label,
            "state": "unavailable",
            "detail": "Non mesuré : réponse /health absente ou invalide",
        }
    if data[field] == "ok":
        return {"label": label, "state": "healthy", "detail": "Connexion vérifiée par /health"}
    if data[field] == "error":
        return {"label": label, "state": "failed", "detail": "Échec de connexion signalé par /health"}
    return {"label": label, "state": "unavailable", "detail": "Valeur /health non reconnue"}


async def collect_status(client):
    frontend, backend, n8n, grafana, prometheus, target = await asyncio.gather(
        probe(client, "Frontend", "http://frontend:80"),
        probe(client, "Backend /health", "http://backend:8000/health", json_body=True),
        probe(client, "n8n", "http://n8n:5678"),
        probe(client, "Grafana /api/health", "http://grafana:3000/api/health", json_body=True),
        probe(client, "Prometheus /-/healthy", "http://prometheus:9090/-/healthy"),
        probe(client, "Prometheus → backend", "http://prometheus:9090/api/v1/targets?state=active", json_body=True),
    )
    if backend["data"] is not None:
        data = backend["data"]
        if data.get("status") != "healthy" or data.get("database") != "ok" or data.get("redis") != "ok":
            backend.update(state="failed", detail="Le backend signale un état non sain ou invalide")
    database = backend_component(backend, "database", "PostgreSQL backend")
    redis = backend_component(backend, "redis", "Redis backend")
    if grafana["data"] is not None:
        if grafana["data"].get("database") != "ok":
            grafana.update(state="failed", detail="Grafana ne confirme pas sa base en état ok")

    if target["state"] == "healthy":
        payload = target["data"]
        valid = (
            payload is not None
            and payload.get("status") == "success"
            and isinstance(payload.get("data"), dict)
            and isinstance(payload["data"].get("activeTargets"), list)
        )
        if not valid:
            target.update(state="failed", detail="Réponse de l'API targets invalide")
        else:
            targets = [
                item for item in payload["data"]["activeTargets"]
                if isinstance(item, dict)
                and isinstance(item.get("labels"), dict)
                and item["labels"].get("job") == "backend"
                and item.get("scrapeUrl") == "http://backend:8000/metrics"
            ]
            if not targets:
                target.update(state="failed", detail="Target backend:8000 absente")
            elif all(item.get("health") == "up" for item in targets):
                target["detail"] = "Scrape du backend confirmé UP par Prometheus"
            else:
                errors = "; ".join(str(item["lastError"]) for item in targets if item.get("lastError"))
                target.update(state="failed", detail="Target backend non UP" + (": " + errors if errors else ""))
    return [frontend, backend, database, redis, n8n, grafana, prometheus, target]


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    checks = await collect_status(request.app.state.http)
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "public_urls": PUBLIC_URLS,
            "checks": checks,
            "rendered_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        },
    )


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "ckpit"}
''',
    'ckpit/app/templates/index.html': r'''<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="15">
  <title>Apex Stack Cockpit</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <main>
    <header>
      <p class="eyebrow">APEX · STACK SELF-HOSTED</p>
      <h1>Apex Stack Cockpit</h1>
      <p>Une machine, plusieurs services. État relevé depuis le réseau interne.</p>
      <a class="refresh" href="/">Actualiser maintenant</a>
    </header>
    <section aria-labelledby="urls-title">
      <h2 id="urls-title">URLs publiques</h2>
      <div class="urls">
        {% for service in public_urls %}
        <a href="{{ service.url }}"><strong>{{ service.name }}</strong><span>{{ service.url }}</span></a>
        {% endfor %}
      </div>
    </section>
    <section aria-labelledby="checks-title">
      <h2 id="checks-title">État des composants</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Composant</th><th>État</th><th>Résultat</th></tr></thead>
          <tbody>
            {% for check in checks %}
            <tr>
              <th scope="row">{{ check.label }}</th>
              <td><span class="badge {{ check.state }}">{% if check.state == 'healthy' %}OK{% elif check.state == 'unavailable' %}Non mesuré{% else %}Échec{% endif %}</span></td>
              <td>{{ check.detail }}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    </section>
    <footer>Rendu : <time>{{ rendered_at }}</time> · Actualisation automatique toutes les 15 secondes.</footer>
  </main>
</body>
</html>
''',
    'ckpit/app/static/style.css': r''':root { font-family: ui-sans-serif, system-ui, sans-serif; color: #e8eef7; background: #0b1220; line-height: 1.5; }
* { box-sizing: border-box; }
body { margin: 0; }
main { max-width: 1100px; margin: 0 auto; padding: 48px 24px; }
.eyebrow { color: #80e0d2; font-size: 12px; font-weight: 700; letter-spacing: 0.12em; }
h1 { font-size: clamp(30px, 5vw, 48px); letter-spacing: -0.035em; margin-bottom: 12px; }
h2 { font-size: 21px; margin: 0 0 20px; }
header p, footer { color: #b2bfd2; }
a { color: #8ce6d8; }
.refresh { display: inline-block; padding: 10px 18px; margin: 10px 0 20px; border: 1px solid #37516c; border-radius: 8px; text-decoration: none; }
section { padding: 24px; margin: 22px 0; background: #131e31; border: 1px solid #2c3d57; border-radius: 16px; }
.urls { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 12px; }
.urls a { padding: 16px; background: #0d1728; border-radius: 8px; text-decoration: none; overflow-wrap: anywhere; }
.urls strong, .urls span { display: block; }
.urls span { font-size: 13px; margin-top: 8px; color: #b2bfd2; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; text-align: left; }
th, td { padding: 14px 12px; border-bottom: 1px solid #2e405c; }
thead th { color: #b2bfd2; font-size: 12px; text-transform: uppercase; letter-spacing: 0.07em; }
tbody th { font-weight: 550; }
td:last-child { font-size: 14px; overflow-wrap: anywhere; }
.badge { display: inline-block; padding: 4px 10px; border-radius: 20px; font-size: 12px; font-weight: 700; white-space: nowrap; }
.healthy { color: #a0edcb; background: #153c32; }
.failed { color: #ffb5bb; background: #4b2530; }
.unavailable { color: #ffe2a0; background: #443d29; }
footer { margin-top: 24px; font-size: 13px; }
a:focus-visible { outline: 3px solid #a0edcb; outline-offset: 4px; }
@media (max-width: 600px) { main { padding: 24px 14px; } section { padding: 16px; } }
''',
    'prometheus/prometheus.yml': r'''global:
  scrape_interval: 5s
  scrape_timeout: 4s

scrape_configs:
  - job_name: backend
    metrics_path: /metrics
    static_configs:
      - targets:
          - backend:8000
''',
    'grafana/provisioning/datasources/prometheus.yml': r'''apiVersion: 1

datasources:
  - name: Prometheus
    uid: prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
''',
    'tests/requirements.txt': r'''-r ../backend/requirements.txt
-r ../ckpit/requirements.txt
pytest==9.1.1
''',
    'tests/test_generator.py': r'''import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "create_project.py"
spec = importlib.util.spec_from_file_location("apex_generator", SCRIPT)
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def run_generator(directory, *arguments):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_generated_project_matches_repository(tmp_path):
    result = run_generator(tmp_path)
    assert result.returncode == 0, result.stderr
    actual = {path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()}
    assert actual == set(generator.FILES)
    assert not (ROOT / "backend/app.py").exists()
    for relative, content in generator.FILES.items():
        assert (tmp_path / relative).read_bytes() == content.encode("utf-8")
        assert (ROOT / relative).read_bytes() == content.encode("utf-8")
        assert relative in result.stdout
    assert (tmp_path / "certs/.gitkeep").read_bytes() == b""
    assert "docker compose up -d --build" in result.stdout
    assert "https://backend.apex.tld/items" in result.stdout


def test_existing_file_stops_all_writes(tmp_path):
    (tmp_path / "README.md").write_text("à conserver", encoding="utf-8")
    result = run_generator(tmp_path)
    assert result.returncode != 0
    assert "--force" in result.stderr
    assert {path.name for path in tmp_path.iterdir()} == {"README.md"}
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "à conserver"


def test_force_updates_project_and_preserves_certificates(tmp_path):
    assert run_generator(tmp_path).returncode == 0
    (tmp_path / "README.md").write_text("ancien README", encoding="utf-8")
    (tmp_path / "backend/app.py").write_text("ancien Flask", encoding="utf-8")
    (tmp_path / "certs/frontend.apex.tld.key").write_text("clé existante", encoding="utf-8")
    result = run_generator(tmp_path, "--force")
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "backend/app.py").exists()
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == generator.FILES["README.md"]
    assert (tmp_path / "certs/frontend.apex.tld.key").read_text(encoding="utf-8") == "clé existante"


@pytest.mark.parametrize("relative", ["backend", "ckpit/app", "grafana/provisioning"])
def test_directory_collision_is_rejected_before_writing(tmp_path, relative):
    collision = tmp_path / relative
    collision.parent.mkdir(parents=True, exist_ok=True)
    collision.write_text("à conserver", encoding="utf-8")
    result = run_generator(tmp_path, "--force")
    assert result.returncode != 0
    assert not (tmp_path / "README.md").exists()
    assert collision.read_text(encoding="utf-8") == "à conserver"


def test_symlink_is_never_overwritten(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("à conserver", encoding="utf-8")
    (tmp_path / "README.md").symlink_to(outside)
    result = run_generator(tmp_path, "--force")
    assert result.returncode != 0
    assert outside.read_text(encoding="utf-8") == "à conserver"
    assert (tmp_path / "README.md").is_symlink()


def test_help_writes_nothing(tmp_path):
    assert run_generator(tmp_path, "--help").returncode == 0
    assert not list(tmp_path.iterdir())
''',
    'tests/test_dashboard.py': r'''import asyncio
import importlib.util
import re
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("apex_cockpit", ROOT / "ckpit/app/main.py")
cockpit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cockpit)


def responses(request, backend_failure=False, target_health="up", target_error=""):
    host, path = request.url.host, request.url.path
    if host == "backend":
        if backend_failure:
            raise httpx.ConnectError("backend inaccessible", request=request)
        return httpx.Response(200, json={"status": "healthy", "database": "ok", "redis": "ok"})
    if host == "grafana":
        return httpx.Response(200, json={"database": "ok"})
    if host == "prometheus" and path == "/api/v1/targets":
        return httpx.Response(200, json={"status": "success", "data": {"activeTargets": [{
            "labels": {"job": "backend"},
            "scrapeUrl": "http://backend:8000/metrics",
            "health": target_health,
            "lastError": target_error,
        }]}})
    if host == "n8n":
        return httpx.Response(302, headers={"Location": "/signin"})
    return httpx.Response(200, text="ok")


def get_checks(handler):
    async def collect():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await cockpit.collect_status(client)
    return asyncio.run(collect())


def test_all_components_healthy_and_n8n_redirect_is_reachable():
    checks = get_checks(responses)
    assert len(checks) == 8
    assert all(check["state"] == "healthy" for check in checks)
    assert next(check for check in checks if check["label"] == "n8n")["detail"] == "HTTP 302"


def test_backend_failure_does_not_invent_database_or_redis_health():
    checks = get_checks(lambda request: responses(request, backend_failure=True))
    by_name = {check["label"]: check for check in checks}
    assert by_name["Backend /health"]["state"] == "failed"
    assert by_name["PostgreSQL backend"]["state"] == "unavailable"
    assert by_name["Redis backend"]["state"] == "unavailable"
    assert by_name["Grafana /api/health"]["state"] == "healthy"


def test_prometheus_server_healthy_does_not_imply_backend_target_up():
    checks = get_checks(lambda request: responses(request, target_health="down", target_error="connection refused"))
    by_name = {check["label"]: check for check in checks}
    assert by_name["Prometheus /-/healthy"]["state"] == "healthy"
    assert by_name["Prometheus → backend"]["state"] == "failed"
    assert "connection refused" in by_name["Prometheus → backend"]["detail"]


def test_backend_reports_independent_dependency_failure():
    def handler(request):
        if request.url.host == "backend":
            return httpx.Response(503, json={"status": "unhealthy", "database": "ok", "redis": "error"})
        return responses(request)
    by_name = {check["label"]: check for check in get_checks(handler)}
    assert by_name["PostgreSQL backend"]["state"] == "healthy"
    assert by_name["Redis backend"]["state"] == "failed"


def test_invalid_target_response_is_reported():
    def handler(request):
        if request.url.path == "/api/v1/targets":
            return httpx.Response(200, json={"status": "success", "data": []})
        return responses(request)
    target = get_checks(handler)[-1]
    assert target["state"] == "failed"
    assert "invalide" in target["detail"]


def test_rendered_dashboard_escapes_errors_and_uses_relative_static_url():
    mock = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: responses(request, target_health="down", target_error="<script>alert(1)</script>")
    ))
    with TestClient(cockpit.app) as client:
        cockpit.app.state.http = mock
        response = client.get("/")
        assert response.status_code == 200
        assert "Apex Stack Cockpit" in response.text
        assert "<script>alert(1)</script>" not in response.text
        assert "&lt;script&gt;" in response.text
        assert 'href="/static/style.css"' in response.text
        assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC", response.text)
        for service in cockpit.PUBLIC_URLS:
            assert service["url"] in response.text
        assert client.get("/static/style.css").status_code == 200
    asyncio.run(mock.aclose())
''',
    'tests/test_backend.py': r'''import importlib.util
import os
from pathlib import Path

import psycopg
import pytest
import redis
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def services():
    # Ces tests utilisent des serveurs réels ; aucune base de substitution.
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    os.environ["REDIS_URL"] = os.environ["TEST_REDIS_URL"]
    os.environ["CACHE_TTL_SECONDS"] = "5"
    spec = importlib.util.spec_from_file_location("apex_backend", ROOT / "backend/app/main.py")
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    worker_spec = importlib.util.spec_from_file_location("apex_worker", ROOT / "worker/worker.py")
    worker = importlib.util.module_from_spec(worker_spec)
    worker_spec.loader.exec_module(worker)
    with TestClient(backend.app) as client:
        yield backend, worker, client
        with backend.connect_database() as connection:
            connection.execute("DELETE FROM items WHERE message LIKE %s", ("integration-test:%",))
        backend.cache.delete(backend.CACHE_KEY)


def test_backend_identity_and_real_dependency_health(services):
    _, _, client = services
    assert client.get("/").json() == {
        "service": "backend", "url": "https://backend.apex.tld", "status": "ok",
        "database": "postgres", "cache": "redis",
    }
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "healthy", "database": "ok", "redis": "ok"}


def test_create_persisted_item_and_read_through_cache(services):
    backend, _, client = services
    backend.cache.delete(backend.CACHE_KEY)
    message = "integration-test: apostrophe ' ; DROP TABLE items; --"
    response = client.post("/items", json={"message": message})
    assert response.status_code == 201
    item = response.json()
    with backend.connect_database() as connection:
        persisted = connection.execute("SELECT message FROM items WHERE id = %s", (item["id"],)).fetchone()
    assert persisted["message"] == message
    first = client.get("/items")
    assert first.status_code == 200
    assert any(row["id"] == item["id"] for row in first.json())
    assert 0 < backend.cache.ttl(backend.CACHE_KEY) <= 5
    before = backend.CACHE_HITS._value.get()
    assert client.get("/items").json() == first.json()
    assert backend.CACHE_HITS._value.get() == before + 1


@pytest.mark.parametrize("message", ["", "   ", "x" * 501])
def test_invalid_messages_are_rejected(services, message):
    assert services[2].post("/items", json={"message": message}).status_code == 422


def test_worker_writes_to_the_same_real_table(services):
    backend, worker, client = services
    worker.initialize_database()
    item_id = worker.insert_tick()
    try:
        backend.cache.delete(backend.CACHE_KEY)
        items = client.get("/items").json()
        assert any(item["id"] == item_id and item["message"].startswith("Worker tick") for item in items)
    finally:
        with backend.connect_database() as connection:
            connection.execute("DELETE FROM items WHERE id = %s", (item_id,))
        backend.cache.delete(backend.CACHE_KEY)


def test_failed_redis_invalidation_rolls_back_sql_write(services, monkeypatch):
    backend, _, client = services
    message = "integration-test: rollback on Redis failure"
    def failed_delete(*args, **kwargs):
        raise redis.exceptions.ConnectionError("Redis unavailable")
    monkeypatch.setattr(backend.cache, "delete", failed_delete)
    assert client.post("/items", json={"message": message}).status_code == 503
    with backend.connect_database() as connection:
        result = connection.execute("SELECT COUNT(*) AS count FROM items WHERE message = %s", (message,)).fetchone()
    assert result["count"] == 0


def test_health_and_metrics_report_database_failure(services, monkeypatch):
    backend, _, client = services
    def failed_database():
        raise psycopg.OperationalError("PostgreSQL unavailable")
    monkeypatch.setattr(backend, "connect_database", failed_database)
    health = client.get("/health")
    assert health.status_code == 503
    assert health.json() == {"status": "unhealthy", "database": "error", "redis": "ok"}
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "backend_database_up 0.0" in metrics.text


def test_redis_failure_is_reported_without_returning_items(services, monkeypatch):
    backend, _, client = services
    def failed_redis(*args, **kwargs):
        raise redis.exceptions.ConnectionError("Redis unavailable")
    monkeypatch.setattr(backend.cache, "get", failed_redis)
    assert client.get("/items").status_code == 503
    monkeypatch.setattr(backend.cache, "ping", failed_redis)
    health = client.get("/health")
    assert health.status_code == 503
    assert health.json()["redis"] == "error"


def test_cors_allows_only_the_public_frontend(services):
    client = services[2]
    headers = {"Origin": "https://frontend.apex.tld", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
    response = client.options("/items", headers=headers)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://frontend.apex.tld"
    headers["Origin"] = "https://unrelated.apex.tld"
    assert client.options("/items", headers=headers).status_code == 400


def test_prometheus_metrics_expose_real_dependency_gauges(services):
    response = services[2].get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "backend_http_requests_total" in response.text
    assert "backend_database_up 1.0" in response.text
    assert "backend_redis_up 1.0" in response.text
''',
    '.github/workflows/ci.yml': r'''name: Apex stack checks

on:
  push:
  pull_request:

permissions:
  contents: read

jobs:
  python:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_DB: app
          POSTGRES_USER: app
          POSTGRES_PASSWORD: app
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U app -d app"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 20
      redis:
        image: redis:7-alpine
        ports:
          - 6379:6379
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 5s
          --health-timeout 3s
          --health-retries 20
    env:
      TEST_DATABASE_URL: postgresql://app:app@127.0.0.1:5432/app
      TEST_REDIS_URL: redis://127.0.0.1:6379/1
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r tests/requirements.txt
      - run: pytest -q tests
      - run: docker compose config --quiet

  frontend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "22"
          cache: npm
          cache-dependency-path: frontend/package-lock.json
      - run: npm ci
        working-directory: frontend
      - run: npm run build
        working-directory: frontend
''',
}


def main():
    parser = argparse.ArgumentParser(
        description="Crée une stack fullstack self-hosted dans le dossier courant."
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Écrase les fichiers générés et retire l'ancien backend/app.py Flask.",
    )
    args = parser.parse_args()
    root = Path.cwd()

    try:
        errors = []
        directories = {
            parent
            for relative in FILES
            for parent in Path(relative).parents
            if parent != Path(".")
        }
        for relative in sorted(directories):
            path = root / relative
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                errors.append(f"Dossier ordinaire attendu : {relative}")

        for relative in (*FILES, *OBSOLETE_FILES):
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
            with path.open("w" if args.force else "x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
            action = "Mis à jour" if existed else "Créé"
            print(f"{action} : {relative}")

        if args.force:
            for relative in OBSOLETE_FILES:
                path = root / relative
                if path.exists():
                    path.unlink()
                    print(f"Retiré : {relative}")
    except OSError as exception:
        print(f"Erreur de génération : {exception}", file=sys.stderr)
        return 1

    print(f"\nProjet créé dans : {root}")
    print("\nPour un Docker local, ajouter ces lignes à /etc/hosts :")
    print(HOSTS_ENTRIES)
    print("\nPréparer les certificats TLS avant le démarrage :")
    print(TLS_COMMANDS)
    print("\nProchaines commandes utiles :")
    print(NEXT_COMMANDS)
    print("\nLa commande docker compose down -v supprime les volumes de données.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
