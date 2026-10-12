# Apex Fullstack — stack self-hosted derrière nginx-proxy

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
