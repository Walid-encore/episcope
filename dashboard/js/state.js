// Shared filter state (VIS-3). Views call set(patch); every subscriber re-renders.
// guard(patch, state) may return a reduced patch; used by the role view (PRV-3) to block disallowed selections.
export function createStore(initial, guard) {
  let state = { ...initial };
  const subs = new Set();
  return {
    get: () => state,
    set(patch) { if (guard) patch = guard(patch, state); state = { ...state, ...patch }; subs.forEach(fn => fn(state)); },
    subscribe(fn) { subs.add(fn); return () => subs.delete(fn); },
  };
}
