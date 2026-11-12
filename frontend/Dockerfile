# 1) build the React app   2) serve it with Caddy (automatic HTTPS) and proxy /api to the backend
FROM node:22-alpine AS build
WORKDIR /src
COPY frontend/package.json frontend/yarn.lock ./
RUN yarn install --frozen-lockfile --network-timeout 600000
COPY frontend/ .
ARG REACT_APP_NAME="Vantage BTC"
ENV REACT_APP_NAME=$REACT_APP_NAME GENERATE_SOURCEMAP=false CI=false
RUN yarn build

FROM caddy:2-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/build /srv
