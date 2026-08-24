const test = require("node:test");
const assert = require("node:assert/strict");
const { managedParams, parseDefaults, restoredParams } = require("../../static/ideas/app.js");

test("managedParams keeps applied filters and omits declared defaults", () => {
  const source = new URLSearchParams("owner=mine&sort=questions&page=2");
  const result = managedParams(source, ["owner", "sort"], parseDefaults("sort=questions"));

  assert.equal(result.toString(), "owner=mine");
});

test("managedParams produces empty state when only defaults are present", () => {
  const source = new URLSearchParams("sort=questions");
  const result = managedParams(source, ["q", "sort"], { sort: "questions" });

  assert.equal(result.toString(), "");
});

test("restoredParams drops pagination while preserving unrelated parameters", () => {
  const result = restoredParams("page=3&view=compact", "owner=mine&sort=updated");

  assert.equal(result.toString(), "view=compact&owner=mine&sort=updated");
});
