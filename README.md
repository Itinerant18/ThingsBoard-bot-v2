# ThingsBoard IoT Chatbot backend

Copy `.env.example` to `.env` and set local Docker URLs. Run `docker compose up --build -d`.
Run `docker compose exec app alembic upgrade head`.
Check `curl http://localhost:8083/health`.
Import with `curl -X POST http://localhost:8083/api/v1/admin/import -H 'Content-Type: application/json' -d '[{"id":"00000000-0000-0000-0000-000000000001","name":"BOI-BRANCH","telemetry":{"full_path":"BOI HO → ZO East → BOI-BRANCH"}}]'`.
# ThingsBoard-bot-v2
