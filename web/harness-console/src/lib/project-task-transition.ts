/** One visual hand-off; task delivery is still owned by project-task-launch. */
type Transition = { ready?: Promise<void>; finished: Promise<void>; skipTransition(): void };
type TransitionDocument = Document & { startViewTransition?: (update: () => Promise<void>) => Transition };
type Handoff = {
  threadId: string;
  ready: () => void;
  done: Promise<void>;
  finish: () => void;
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
  const doc = document as TransitionDocument;
  if (!doc.startViewTransition || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
    navigate();
    return;
  }
  handoff?.finish();
  let ready!: () => void;
  let finish!: () => void;
  const destination = new Promise<void>(resolve => { ready = resolve; });
  const done = new Promise<void>(resolve => { finish = resolve; });
  const current: Handoff = { threadId, ready, done, finish, names: [] };
  handoff = current;
  nameElement(current, '[data-project-launch-composer] .aui-composer-root', 'project-composer');
  nameElement(current, '.task-sidebar', 'project-sidebar');
  nameElement(current, '[data-project-resources]', 'project-resources');
  let transition: Transition | undefined;
  let navigated = false;
  // A failed or slow page load must never leave the browser's snapshot frozen.
  const timeout = window.setTimeout(() => { ready(); transition?.skipTransition(); }, 3500);
  const clean = () => {
    window.clearTimeout(timeout);
    for (const { element, previous } of current.names) element.style.viewTransitionName = previous;
    if (handoff === current) handoff = undefined;
    finish();
  };
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

/** Called only once the target thread has restored its draft and execution context. */
export async function waitForProjectTaskTransition(threadId: string) {
  const current = handoff;
  if (!current || current.threadId !== threadId) return;
  nameElement(current, '.task-content-shell .harness-composer-shell .aui-composer-root', 'project-composer');
  nameElement(current, '.task-sidebar', 'project-sidebar');
  current.ready();
  await current.done;
}
