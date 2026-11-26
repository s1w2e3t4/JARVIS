# React + Vite

This template provides a minimal setup to get React working in Vite with HMR and some Oxlint rules.

Currently, two official plugins are available:

- [@vitejs/plugin-react](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react) uses [Oxc](https://oxc.rs)
- [@vitejs/plugin-react-swc](https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react-swc) uses [SWC](https://swc.rs/)

## React Compiler

The React Compiler is not enabled on this template because of its impact on dev & build performances. To add it, see [this documentation](https://react.dev/learn/react-compiler/installation).

## Expanding the Oxlint configuration

If you are developing a production application, we recommend using TypeScript with type-aware lint rules enabled. Check out the [TS template](https://github.com/vitejs/vite/tree/main/packages/create-vite/template-react-ts) for information on how to integrate TypeScript and Oxlint's TypeScript related rules in your project.

## Local JARVIS accounts

From the repository root, install the Python dependencies and start FastAPI.

```powershell
.\jarvisenv\Scripts\python.exe -m pip install -r requirements.txt
.\jarvisenv\Scripts\python.exe -m uvicorn backend.api:app --port 8000
```

In another terminal, run `npm.cmd ci` and `npm.cmd run dev` from `frontend`. Open
`http://localhost:5173` and choose **Settings → Account**. Vite proxies account
requests to FastAPI. SQLite stores local accounts and sessions in
`backend/data/accounts.sqlite3` by default. Set `JARVIS_ACCOUNT_DB` to use
another path.

The chat endpoint still uses the desktop assistant's process-wide conversation
context. Account controls currently manage sign-in state. Before public use,
isolate chat by user, add authentication rate limits, serve account routes
on the frontend origin over HTTPS, and set `JARVIS_SECURE_COOKIES=1`.
