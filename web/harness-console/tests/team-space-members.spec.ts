import { describe, expect, it } from "vitest";
import { spaceMemberDirectory } from "../src/lib/team-space-members";

describe("collaboration space member directory", () => {
  it("only offers current space members as Agent ACL grantees", () => {
    const directory = [
      { user: { user_id: "owner", display_name: "Owner" } },
      { user: { user_id: "member", display_name: "Member" } },
      { user: { user_id: "tenant-only", display_name: "Tenant only" } },
    ];

    expect(
      spaceMemberDirectory(directory, [
        { userId: "owner" },
        { userId: "member" },
      ]).map((entry) => entry.user.user_id),
    ).toEqual(["owner", "member"]);
  });

  it("returns no ACL candidate before a user joins the space", () => {
    expect(
      spaceMemberDirectory(
        [{ user: { user_id: "tenant-only" } }],
        [],
      ),
    ).toEqual([]);
  });
});
