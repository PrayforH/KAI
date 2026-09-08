type EditMessageForkActions = {
  fork: () => Promise<{ threadId: string }>;
  send: (threadId: string) => Promise<void>;
  navigate: (threadId: string) => void | Promise<void>;
};

export async function forkAndSendEditedMessage(actions: EditMessageForkActions): Promise<void> {
  const { threadId } = await actions.fork();
  await actions.send(threadId);
  await actions.navigate(threadId);
}
