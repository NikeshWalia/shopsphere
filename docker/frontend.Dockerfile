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

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD wget -qO- http://localhost/healthz || exit 1

CMD ["nginx", "-g", "daemon off;"]
