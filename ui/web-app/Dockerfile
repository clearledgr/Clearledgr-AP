# Production image for the web-app Railway service.
#
# Two-stage build:
#   1. builder — Vite produces the static SPA into /app/dist.
#   2. runtime — Express + http-proxy-middleware serves /app/dist and
#      reverse-proxies /api, /auth, etc. to the api service.
#
# Build context: repo root (Railway sends the whole repo). Everything
# we need lives under ui/web-app/.

# ── Stage 1: build the SPA ──
FROM node:22-alpine AS builder

WORKDIR /app

# Copy only what we need for the install step so the layer cache works.
COPY ui/web-app/package.json ui/web-app/package-lock.json ./
RUN npm ci --include=dev

# Copy sources + config.
COPY ui/web-app/index.html ./
COPY ui/web-app/vite.config.js ./
COPY ui/web-app/server.js ./
COPY ui/web-app/src ./src

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
