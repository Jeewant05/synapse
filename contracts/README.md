# Shared contracts

Generated from Pydantic models and FastAPI routes with `npm run contracts`:

- `schema.json`: shared model definitions, including adapter results.
- `openapi.json`: currently implemented HTTP endpoints.
- `example-state.json`: initial OAuth objective, two workstreams, and three local decisions.
- `../ui/src/api.generated.ts`: UI API types generated from OpenAPI.

Commit generated changes together with their Python source. Future coordinator request schemas will be added with their endpoints; this foundation does not claim those endpoints exist.
