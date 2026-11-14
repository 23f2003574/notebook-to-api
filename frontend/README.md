# React + Vite

This template provides a minimal setup to get React working in Vite with HMR and some ESLint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the ESLint configuration

If you are developing a production application, we recommend using TypeScript with type-aware lint rules enabled. Check out the [TS template](https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts) for information on how to integrate TypeScript and [`typescript-eslint`](https://typescript-eslint.io) in your project.


## Configuration

The dashboard talks to two services, configured at build time (see `.env.example`):

- `VITE_DASHBOARD_API_URL` - the dashboard backend: an absolute `http(s)` URL (a base path such as
  `https://example.com/dashboard` is kept) or a same-origin path such as `/dashboard`. Unset, it is
  `http://localhost:8001` under `npm run dev` and the page's own origin in a production build, which
  suits `python -m backend.dashboard` serving the built frontend.
- `VITE_APP_URL` - the compiled application used by the endpoint tester and its `/docs` link. Unset,
  it is `http://localhost:8000` under `npm run dev`; it is required in a production build.

Trailing slashes are ignored. A value that is not an absolute `http(s)` URL or a `/path` (for example
`localhost:8001`), or a missing `VITE_APP_URL` in a production build, shows a configuration banner in
the dashboard instead of sending requests to the wrong place. When the dashboard and the services use
different origins, each service must allow the dashboard's origin in its CORS settings
(`NOTEBOOK_API_ALLOWED_ORIGINS`).
