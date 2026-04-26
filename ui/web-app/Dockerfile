# Production image for the web-app Railway service.
#
# Two-stage build:
#   1. builder — Vite produces the static SPA into /app/dist.
#   2. runtime — Express + http-proxy-middleware serves /app/dist and
#      reverse-proxies /api, /auth, etc. to the api service.
#
# Build context: this directory (ui/web-app/). The Railway service
# is deployed via `railway up ui/web-app --path-as-root --service web-app`,
# which makes the tarball root equal to ui/web-app/. All COPY paths
# below are therefore relative to that.

# ── Stage 1: build the SPA ──
FROM node:22-alpine AS builder

WORKDIR /app

# Copy only what we need for the install step so the layer cache works.
COPY package.json package-lock.json ./
RUN npm ci --include=dev

# Copy sources + config.
COPY index.html ./
COPY vite.config.js ./
COPY server.js ./
COPY src ./src

# Produce dist/.
RUN npm run build

# Drop devDependencies for the final image (keep express + proxy only).
RUN npm prune --omit=dev

# ── Stage 2: minimal runtime ──
FROM node:22-alpine AS runtime

WORKDIR /app

# Bring across the prod dependency tree, the built dist/, and the server.
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/dist ./dist
COPY --from=builder /app/server.js ./server.js
COPY --from=builder /app/package.json ./package.json

ENV NODE_ENV=production

EXPOSE 8080

CMD ["node", "server.js"]
