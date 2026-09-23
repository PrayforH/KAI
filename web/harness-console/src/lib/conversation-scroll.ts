/** Follow layout changes, not individual tokens. Only reader intent pauses follow. */
export function attachConversationScroll(viewport: HTMLElement) {
  const tolerance = 24;
  let following = true;
  let frame: number | null = null;
  let disposed = false;
  let lastTop = viewport.scrollTop;
  let lastHeight = viewport.scrollHeight;
  let touchY: number | null = null;
  const atBottom = () => viewport.scrollHeight - viewport.clientHeight - viewport.scrollTop <= tolerance;

  function schedule() {
    if (disposed || frame !== null) return;
    frame = requestAnimationFrame(() => {
      frame = null;
      if (!following) return;
      viewport.scrollTo({ top: viewport.scrollHeight, behavior: "instant" });
      lastTop = viewport.scrollTop;
      lastHeight = viewport.scrollHeight;
    });
  }
  function resume() { following = true; schedule(); }
  function onScroll() {
    // This listener is intentionally not capturing: scrolling a thought/result
    // preview must not change the outer conversation's follow state.
    const top = viewport.scrollTop;
    const height = viewport.scrollHeight;
    if (top < lastTop && height === lastHeight) following = false;
    else if (atBottom()) following = true;
    lastTop = top;
    lastHeight = height;
  }
  function isNestedScroller(target: EventTarget | null) {
    let element = target instanceof Element ? target : null;
    while (element && element !== viewport) {
      if (element.scrollHeight > element.clientHeight && /auto|scroll/.test(getComputedStyle(element).overflowY)) return true;
      element = element.parentElement;
    }
    return false;
  }
  function onWheel(event: WheelEvent) {
    if (event.deltaY < 0 && !isNestedScroller(event.target)) following = false;
  }
  function onTouchStart(event: TouchEvent) { touchY = event.touches[0]?.clientY ?? null; }
  function onTouchMove(event: TouchEvent) {
    const next = event.touches[0]?.clientY;
    if (next !== undefined && touchY !== null && next > touchY && !isNestedScroller(event.target)) following = false;
    touchY = next ?? null;
  }
  function onKeyDown(event: KeyboardEvent) {
    const target = event.target instanceof Element ? event.target : null;
    if (target?.closest('input, textarea, [contenteditable="true"]') || isNestedScroller(target)) return;
    if (["ArrowUp", "PageUp", "Home"].includes(event.key) || (event.key === " " && event.shiftKey)) following = false;
    if (event.key === "End") resume();
  }
  function onClick(event: MouseEvent) {
    if (event.target instanceof Element && event.target.closest(".aui-thread-scroll-to-bottom")) resume();
  }

  // The viewport's own size doesn't change when messages/images grow. Observe
  // message roots and the sticky composer as well, including late image loads.
  const observed = new Set<Element>();
  const resize = new ResizeObserver(schedule);
  resize.observe(viewport);
  function syncChildren() {
    const children = new Set(viewport.children);
    for (const element of observed) if (!children.has(element)) { resize.unobserve(element); observed.delete(element); }
    for (const element of children) if (!observed.has(element)) { resize.observe(element); observed.add(element); }
  }
  const mutations = new MutationObserver(() => { syncChildren(); schedule(); });
  mutations.observe(viewport, { childList: true, subtree: true, characterData: true, attributes: true });
  syncChildren();
  viewport.addEventListener("scroll", onScroll, { passive: true });
  viewport.addEventListener("wheel", onWheel, { passive: true });
  viewport.addEventListener("touchstart", onTouchStart, { passive: true });
  viewport.addEventListener("touchmove", onTouchMove, { passive: true });
  viewport.addEventListener("keydown", onKeyDown);
  viewport.addEventListener("click", onClick);
  schedule();
  return {
    resume,
    dispose() {
      disposed = true;
      if (frame !== null) cancelAnimationFrame(frame);
      resize.disconnect(); mutations.disconnect();
      viewport.removeEventListener("scroll", onScroll);
      viewport.removeEventListener("wheel", onWheel);
      viewport.removeEventListener("touchstart", onTouchStart);
      viewport.removeEventListener("touchmove", onTouchMove);
      viewport.removeEventListener("keydown", onKeyDown);
      viewport.removeEventListener("click", onClick);
    },
  };
}
