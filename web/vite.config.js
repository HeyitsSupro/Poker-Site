import { readFileSync } from 'node:fs';
import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

/**
 * The site is named in exactly one place: SITE_NAME in public/config.js.
 * The web app manifest needs the name too, so it is generated from there at
 * build time rather than kept in a second file that can drift.
 */
function webManifest() {
  return {
    name: 'generate-web-manifest',
    generateBundle() {
      const source = readFileSync(new URL('./public/config.js', import.meta.url), 'utf8');
      const name = /SITE_NAME:\s*['"]([^'"]+)['"]/.exec(source)?.[1] ?? 'Poker table';

      // The single most likely thing to go wrong on a first deploy: the
      // site goes up with the backend address it was developed against,
      // and then works for the person who built it and nobody else,
      // because "localhost" means a different machine to every device.
      const backend = /BACKEND_URL:\s*['"]([^'"]+)['"]/.exec(source)?.[1] ?? '';
      if (/^https?:\/\/(localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0)\b/.test(backend)) {
        this.warn(
          `BACKEND_URL is ${backend}. That only works on the machine running ` +
            'the server — phones and other people will not be able to reach ' +
            'it. Set it to the public backend address in web/public/config.js ' +
            '(or edit docs/config.js after building) before sharing the link.',
        );
      }
      this.emitFile({
        type: 'asset',
        fileName: 'manifest.webmanifest',
        source: JSON.stringify(
          {
            name,
            short_name: name,
            description: 'A private play-money poker table for friends.',
            start_url: './',
            scope: './',
            display: 'standalone',
            orientation: 'any',
            background_color: '#141317',
            theme_color: '#141317',
            icons: [
              { src: 'icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' },
              { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
              { src: 'icon-512.png', sizes: '512x512', type: 'image/png' },
            ],
          },
          null,
          2,
        ),
      });
    },
  };
}

export default defineConfig({
  plugins: [svelte(), webManifest()],
  // Relative asset URLs, so the same build works on a user page
  // (user.github.io) and a project page (user.github.io/repo/).
  base: './',
  build: {
    // GitHub Pages serves this folder straight from the repo's main branch.
    outDir: '../docs',
    emptyOutDir: true,
    target: 'es2022',
  },
  server: { port: 5173 },
});
