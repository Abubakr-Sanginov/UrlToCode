FROM node:22-bullseye-slim

WORKDIR /app

# pnpm, matching the lockfile in this repository. This used to copy a
# yarn.lock that does not exist, so the image never built.
RUN corepack enable

# Skip the Chromium download: the crawler runs on the backend, and pulling
# a browser through every frontend rebuild is a minute for nothing.
ENV PUPPETEER_SKIP_DOWNLOAD=true

# Dependencies first, so a source-only change reuses the install layer.
COPY package.json pnpm-lock.yaml /app/
RUN pnpm install --frozen-lockfile

COPY ./ /app/

# A built site, not a dev server. `vite dev` serves unbundled modules and
# is not meant to face the public.
RUN pnpm build

EXPOSE 5173
CMD ["pnpm", "preview", "--host", "0.0.0.0", "--port", "5173"]
