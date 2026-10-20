/** Navigation owns the visual hand-off; the runtime can start work during it. */
type Transition = { ready?: Promise<void>; finished: Promise<void>; skipTransition(): void };
type TransitionDocument = Document & { startViewTransition?: (update: () => Promise<void>) => Transition };
type Handoff = {
  threadId: string;
  ready: () => void;
  cancel: () => void;
  revealTimer?: number;
  revealing?: boolean;
  names: Array<{ element: HTMLElement; previous: string }>;
};
let handoff: Handoff | undefined;

function nameElement(current: Handoff, selector: string, name: string) {
  const element = document.querySelector<HTMLElement>(selector);
  if (!element || current.names.some(item => item.element === element)) return;
  current.names.push({ element, previous: element.style.viewTransitionName });
  element.style.viewTransitionName = name;
}

export function startProjectTaskTransition(threadId: string, navigate: () => void) {
  handoff?.cancel();
  const doc = document as TransitionDocument;
  if (!doc.startViewTransition || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
    navigate();
    return;
  }
  let ready!: () => void;
  const destination = new Promise<void>(resolve => { ready = resolve; });
  const current: Handoff = { threadId, ready, cancel: () => undefined, names: [] };
  handoff = current;
  document.documentElement.setAttribute("data-project-task-transition", "");
  nameElement(current, '[data-project-launch-composer] .aui-composer-root', 'project-composer');
  nameElement(current, '.task-sidebar', 'project-sidebar');
  nameElement(current, '[data-project-resources]', 'project-resources');
  let transition: Transition | undefined;
  let navigated = false;
  let cleaned = false;
  const clean = () => {
    if (cleaned) return;
    cleaned = true;
    window.clearTimeout(timeout);
    if (current.revealTimer !== undefined) window.clearTimeout(current.revealTimer);
    for (const { element, previous } of current.names) element.style.viewTransitionName = previous;
    if (handoff === current) {
      handoff = undefined;
      document.documentElement.removeAttribute("data-project-task-transition");
    }
    ready();
  };
  current.cancel = () => { transition?.skipTransition(); clean(); };
  // Slow navigation falls back to the live loading UI rather than freezing it.
  const timeout = window.setTimeout(current.cancel, 1200);
  try {
    transition = doc.startViewTransition(async () => {
      navigated = true;
      navigate();
      await destination;
    });
    void transition.ready?.catch(() => undefined);
    void transition.finished.then(clean, clean);
  } catch {
    clean();
    if (!navigated) navigate();
  }
}

/** Capture the accepted prompt as a message, not as a draft that clears after animation. */
export function revealProjectTaskTransition(threadId: string) {
  const current = handoff;
  if (!current || current.threadId !== threadId || current.revealing) return;
  current.revealing = true;
  // Native View Transitions pause animation frames while updateCallback is pending.
  // Yield a task so React can commit send/reset without waiting on that paused clock.
  current.revealTimer = window.setTimeout(() => {
    if (handoff !== current) return;
    nameElement(current, '.task-content-shell .harness-composer-shell .aui-composer-root', 'project-composer');
    nameElement(current, '.task-sidebar', 'project-sidebar');
    current.ready();
  }, 0);
}
