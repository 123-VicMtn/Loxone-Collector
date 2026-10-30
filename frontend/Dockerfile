# Image du frontend : le build Vite servi par Caddy, qui est aussi le
# reverse proxy de l'API (deploy/Caddyfile, monté par compose.yaml).

FROM node:22-alpine AS build
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /src/dist /srv
