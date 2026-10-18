// @vitest-environment jsdom
import { act, StrictMode, useSyncExternalStore } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
const mock = vi.hoisted(() => ({ resources: vi.fn(), summary: vi.fn(), catalog: vi.fn(), sidebar: null as any, run: { threadId: "", runId: "" }, agents: [{ name: "lead-agent", version: "1", ownerUserId: "owner", displayName: "助手", canChat: true }] }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: (url: string) => { window.history.pushState({}, "", url); window.dispatchEvent(new Event("location-test")); } }),
  useSearchParams: () => new URLSearchParams(useSyncExternalStore(listener => { window.addEventListener("location-test", listener); return () => window.removeEventListener("location-test", listener); }, () => window.location.search)),
}));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));
vi.mock("../src/components/auth-provider", () => ({ AuthProvider: ({children}: any) => children, useAuth: () => ({user:{user_id:"owner"}}) }));
vi.mock("../src/lib/interface-preferences", () => ({useInternalAgentsPreference: () => [false]}));
vi.mock("../src/lib/studio-client", () => ({projectClient:{resources: mock.resources}}));
vi.mock("../src/lib/sidebar-state", () => ({useSidebarProjects: () => [[{projectId:"a",name:"项目 A"},{projectId:"b",name:"项目 B"}],vi.fn()]}));
vi.mock("../src/lib/activity-store", () => ({useRunActivity: () => null, useRunViewModel: () => null}));
vi.mock("../src/lib/run-stream-store", () => ({useRunStream: () => mock.run}));
vi.mock("../src/lib/task-version", () => ({persistTaskVersion: vi.fn(),taskVersionBusy:()=>false}));
vi.mock("../src/lib/project-space", async original => ({...await original<object>(), loadTaskSummary: mock.summary}));
vi.mock("../src/lib/task-history", () => ({peekCachedTasks:()=>[]}));
vi.mock("../src/lib/task-agent-catalog", () => ({
  loadTaskAgentCatalog:mock.catalog, chatUsableAgents:(agents:any)=>agents,
  agentItemKey:(a:any)=>a.name+"@"+a.version, runtimeAgentKey:(a:any)=>a.name+"@"+a.version,
  currentSystemAssistant:(_a:any,system:any)=>system,
  findTaskAgent:(agents:any[],coordinates:any)=>agents.find(a=>a.name===coordinates.name&&a.version===coordinates.version),
}));
vi.mock("../src/lib/task-model-catalog", () => ({loadTaskModelRoutes:async()=>[],loadTaskModelOverride:()=>null,saveTaskModelOverride:vi.fn()}));
vi.mock("../src/components/assistant-runtime-shell", () => ({AssistantRuntimeShell:(props:any)=><div data-runtime-thread={props.threadId} data-runtime-project={props.projectId}>{props.children}</div>}));
vi.mock("../src/components/agent-thread", () => ({AgentThread:({projectName}:any)=><div>{projectName ?? "普通任务"}<textarea aria-label="统一输入框" /></div>}));
vi.mock("../src/components/task-sidebar", () => ({TaskSidebar:(props:any)=>{mock.sidebar=props;return <nav aria-label="原有左侧"><button onClick={props.onNewTask}>新任务</button></nav>;}}));
vi.mock("../src/components/workbench-rail", () => ({WorkbenchRail:()=> <aside>右侧项目配置与成果</aside>}));
vi.mock("../src/components/productivity-command-center", () => ({ProductivityCommandCenter:()=>null}));
vi.mock("../src/components/run-trace-console", () => ({RunTraceConsole:()=>null}));
vi.mock("../src/components/task-agent-switcher", () => ({TaskAgentSwitcher:()=>null,taskAgentSwitchMode:()=>"agent"}));
vi.mock("../src/components/task-header-actions", () => ({TaskDetailsPopover:()=>null,TaskHeaderActions:()=>null}));
import Home from "../src/app/page";
(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
let host:HTMLDivElement;
let root:ReturnType<typeof createRoot>;
async function navigate(url:string) { await act(async()=>{window.history.pushState({},"",url);window.dispatchEvent(new Event("location-test"));}); }
const runtime = () => host.querySelector("[data-runtime-thread]");
beforeEach(()=>{
  vi.clearAllMocks();
  const values = new Map<string,string>();
  const storage={getItem:(k:string)=>values.get(k)??null,setItem:(k:string,v:string)=>values.set(k,v),removeItem:(k:string)=>values.delete(k)};
  vi.stubGlobal("localStorage",storage);
  vi.stubGlobal("matchMedia",()=>({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()}));
  mock.run={threadId:"",runId:""};
  mock.resources.mockImplementation(async(id:string)=>({project:{projectId:id,name:`项目 ${id.toUpperCase()}`}}));
  mock.summary.mockResolvedValue(null);
  mock.catalog.mockResolvedValue({agents:mock.agents,defaultAgent:mock.agents[0]});
  window.history.replaceState({},"","/?project=a");
  host=document.createElement("div");document.body.append(host);root=createRoot(host);
});
afterEach(()=>{act(()=>root.unmount());host.remove();vi.unstubAllGlobals();});
it("restores each project's local draft and clears project context for global New Task",async()=>{
  await act(async()=>root.render(<Home/>));
  const a=runtime()!.getAttribute("data-runtime-thread");
  expect(runtime()!.getAttribute("data-runtime-project")).toBe("a");
  expect(host.querySelectorAll("textarea")).toHaveLength(1);
  await navigate("/?project=b");
  expect(runtime()!.getAttribute("data-runtime-thread")).not.toBe(a);
  expect(runtime()!.getAttribute("data-runtime-project")).toBe("b");
  await navigate("/?project=a");
  expect(runtime()!.getAttribute("data-runtime-thread")).toBe(a);
  await act(async()=>mock.sidebar.onNewTask());
  expect(runtime()!.getAttribute("data-runtime-project")).toBeNull();
  expect(runtime()!.getAttribute("data-runtime-thread")).not.toBe(a);
  await act(async()=>window.dispatchEvent(new Event("location-test")));
  expect(runtime()!.getAttribute("data-runtime-project")).toBeNull();
});
it("keeps the executing runtime and project stable when its first Run makes the URL durable",async()=>{
  await act(async()=>root.render(<Home/>));
  const thread=runtime()!.getAttribute("data-runtime-thread")!;
  mock.run={threadId:thread,runId:"run-1"};
  await act(async()=>root.render(<Home/>));
  expect(window.location.search).toBe(`?thread=${thread}`);
  await act(async()=>window.dispatchEvent(new Event("location-test")));
  expect(runtime()!.getAttribute("data-runtime-thread")).toBe(thread);
  expect(runtime()!.getAttribute("data-runtime-project")).toBe("a");
});
it("does not carry a previous project's context into an ordinary durable task",async()=>{
  await act(async()=>root.render(<Home/>));
  mock.summary.mockResolvedValue({thread_id:"ordinary",title:"普通任务",agent_name:"lead-agent",agent_version:"1",agent_owner_user_id:"owner",project_id:null});
  await navigate("/?thread=ordinary");
  expect(runtime()!.getAttribute("data-runtime-thread")).toBe("ordinary");
  expect(runtime()!.getAttribute("data-runtime-project")).toBeNull();
});
it("blocks the composer when the selected project is inaccessible",async()=>{
  mock.resources.mockRejectedValue(new Error("无项目访问权限"));
  await act(async()=>root.render(<Home/>));
  expect(runtime()).toBeNull();
  expect(host.textContent).toContain("无项目访问权限");
});

it("restores a project under Strict Mode effect replay", async () => {
  await act(async () => root.render(<StrictMode><Home /></StrictMode>));
  expect(runtime()!.getAttribute("data-runtime-project")).toBe("a");
  expect(host.querySelectorAll("textarea")).toHaveLength(1);
});
