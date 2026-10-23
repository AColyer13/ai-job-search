import { afterEach, describe, expect, test } from "bun:test";
import { runSearch } from "../src/commands/search";
import { jobageToTPR } from "../src/helpers";

const originalFetch = globalThis.fetch;
const originalStdoutWrite = process.stdout.write;

function searchCard(id: string, title: string): string {
  return `<li>
    <div data-entity-urn="urn:li:jobPosting:${id}">
      <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/${id}"></a>
      <h3 class="base-search-card__title">${title}</h3>
    </div>
  </li>`;
}

afterEach(() => {
  globalThis.fetch = originalFetch;
  process.stdout.write = originalStdoutWrite;
});

describe("jobageToTPR", () => {
  test("snaps up to LinkedIn's 1, 7, 14, and 30 day windows", () => {
    expect(jobageToTPR(1)).toBe("r86400");
    expect(jobageToTPR(5)).toBe("r604800");
    expect(jobageToTPR(7)).toBe("r604800");
    expect(jobageToTPR(14)).toBe("r1209600");
    expect(jobageToTPR(15)).toBe("r2592000");
    expect(jobageToTPR(30)).toBe("r2592000");
    expect(jobageToTPR(9999)).toBeNull();
  });
});

describe("runSearch", () => {
  test("--limit 0 emits zero results", async () => {
    globalThis.fetch = (async () => new Response(searchCard("123456", "Engineer"))) as typeof fetch;

    let stdout = "";
    process.stdout.write = ((chunk: string | Uint8Array) => {
      stdout += chunk.toString();
      return true;
    }) as typeof process.stdout.write;

    const code = await runSearch({
      location: "Copenhagen, Denmark",
      jobage: 9999,
      page: 1,
      limit: 0,
      format: "json",
    });

    expect(code).toBe(0);
    expect(JSON.parse(stdout).results).toHaveLength(0);
  });
});
