# syntax=docker/dockerfile:1
#
# Storefront. Built with Node, served by nginx - the runtime image contains
# static assets and nothing else, so there is no Node process in production and
# no npm dependency tree to keep patched.

FROM node:22-alpine AS builder

WORKDIR /build

# Dependencies first: editing a component must not reinstall node_modules.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine AS runtime

COPY --from=builder /build/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

# 127.0.0.1, not localhost: nginx binds 0.0.0.0:80 (IPv4 only), while localhost
# resolves to ::1 first on some Docker hosts - Docker Desktop on Windows among
# them. The container then reported unhealthy while serving traffic perfectly
# well. An explicit IPv4 address removes the dependency on resolver ordering.
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD wget -qO- http://127.0.0.1/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
