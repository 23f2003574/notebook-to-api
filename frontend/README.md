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

- `VITE_DASHBOARD_API_URL` - the dashboard backend (default `http://localhost:8001`).
- `VITE_APP_URL` - the compiled application used by the endpoint tester and its `/docs` link
  (default `http://localhost:8000`).

Trailing slashes are ignored. The browser calls these URLs directly, so each service must
allow this origin in its CORS settings (`NOTEBOOK_API_ALLOWED_ORIGINS`).
