# Single-host reverse proxy stack

Cette stack représente une seule machine qui héberge plusieurs services web
Docker derrière nginx-proxy.

## Prérequis

- Docker Engine et Docker Compose 2.23.1 ou supérieur.
- Les ports 80 et 443 disponibles sur la machine.
- OpenSSL pour générer les certificats locaux.

## Sous-domaines

- https://frontend.apex.tld
- https://backend.apex.tld
- https://grafana.apex.tld
- https://n8n.apex.tld
- https://ckpit.apex.tld

## DNS et utilisation locale

Tous les sous-domaines pointent vers la même machine. En déploiement, leurs
enregistrements DNS pointent vers l'adresse IP de cette machine.

En local, ajouter ces lignes à `/etc/hosts` sur la machine cliente :

```text
127.0.0.1 frontend.apex.tld
127.0.0.1 backend.apex.tld
127.0.0.1 grafana.apex.tld
127.0.0.1 n8n.apex.tld
127.0.0.1 ckpit.apex.tld
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
`GET /health` retourne `{"status": "healthy"}`.

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

Le dossier contient initialement uniquement `.gitkeep`. Préparer les
certificats avant de démarrer la stack pour que les URLs HTTPS fonctionnent.

Commande exemple pour générer un certificat wildcard auto-signé utilisable
localement, puis copier le couple `apex.tld.crt`/`apex.tld.key` vers les noms
attendus pour chaque sous-domaine :

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
