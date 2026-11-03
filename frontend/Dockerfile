# Só compila: o resultado é copiado para a pasta servida pelo nginx do servidor (ver infra/deploy/implantar.sh).
FROM node:26-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY . .
RUN npm run build

FROM scratch AS dist
COPY --from=build /app/dist /
