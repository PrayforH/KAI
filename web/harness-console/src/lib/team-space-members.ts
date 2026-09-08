type DirectoryEntry = { user: { user_id: string } };
type SpaceMembership = { userId: string };

export function spaceMemberDirectory<T extends DirectoryEntry>(
  directory: readonly T[],
  members: readonly SpaceMembership[],
): T[] {
  const memberIds = new Set(members.map((member) => member.userId));
  return directory.filter((entry) => memberIds.has(entry.user.user_id));
}
