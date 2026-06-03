# /plan — Architecture and implementation planning

Design the solution before writing code.

## What to do

- **Identify the target architecture** (layered, event-driven, distributed, etc.)
- **Map data flows**: inputs → processing → outputs; show state transformations
- **List key modules/components**: what they do, how they communicate, responsibility boundaries
- **Design the main data structures** (schemas, types, class hierarchies)
- **Call out integration points**: where does this connect to existing code?
- **Identify failure modes**: what could go wrong, and how do we handle it?
- **Sketch file structure**: where does each piece live in the codebase?

## Format

Use diagrams (ASCII or prose) plus bullet lists:
- Architecture diagram (ASCII box/arrow is fine)
- Component responsibilities (3-4 sentences per component)
- Data structure sketches (TypedDict, dataclass, or schema notation)
- Integration notes (what do we need from existing code?)
- Error handling strategy (fallback, retry, logging)
- File layout (what modules, where they go)

## Example

```
## Component: Cache Layer

```
    ┌─────────────┐      ┌──────────────┐      ┌─────────────┐
    │   Request   │──→   │  Cache Hit?  │──→   │  Response   │
    └─────────────┘      └──────────────┘      └─────────────┘
                              │ No
                              ↓
                         ┌──────────────┐      ┌─────────────┐
                         │  Fetch Data  │──→   │  Store in   │
                         └──────────────┘      │  Cache(TTL) │
                                              └─────────────┘
```

### Cache Entry (dataclass)

```python
@dataclass
class CacheEntry:
    key: str
    value: Any
    expires_at: datetime
```

### Error Handling
- On fetch failure: try up to 3 times with exponential backoff
- If all retries fail: return stale cache entry if available, else raise error
- Log all cache misses for metrics
```
