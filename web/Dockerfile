FROM node:20-bookworm-slim AS deps

WORKDIR /app

COPY web/package.json /app/package.json
COPY web/package-lock.json /app/package-lock.json

RUN npm ci

FROM node:20-bookworm-slim AS builder

WORKDIR /app

ENV NODE_ENV=production \
    INTERNAL_API_BASE_URL=http://api:8000/api/v1 \
    NEXT_PUBLIC_API_BASE_URL=/api/v1 \
    NEXT_PUBLIC_SSE_BASE_URL=/api/v1

COPY --from=deps /app/node_modules /app/node_modules
COPY web /app

RUN npm run build

FROM node:20-bookworm-slim AS runtime

WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000

COPY --from=builder /app/.next/standalone /app
COPY --from=builder /app/.next/static /app/.next/static
COPY --from=builder /app/public /app/public

EXPOSE 3000

CMD ["node", "server.js"]
