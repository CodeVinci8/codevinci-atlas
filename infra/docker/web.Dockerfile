# Web: сборка Vite → раздача через непривилегированный Nginx (Master Spec §7).
FROM node:24-slim AS build
WORKDIR /web
RUN corepack enable pnpm
# Установка по локу (voспроизводимо, esbuild разрешён через pnpm-workspace.yaml).
COPY apps/web/package.json apps/web/pnpm-lock.yaml apps/web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/web/ ./
RUN pnpm build

# Непривилегированный Nginx (uid 101, слушает 8080, без root).
FROM nginxinc/nginx-unprivileged:1.27-alpine AS runtime
COPY infra/docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 8080
