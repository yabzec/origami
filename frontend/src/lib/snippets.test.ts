import { describe, expect, it } from "vitest";
import { splitHighlights } from "./snippets";

describe("splitHighlights", () => {
  it("passes plain text through", () => {
    expect(splitHighlights("nothing special")).toEqual([{ text: "nothing special", highlighted: false }]);
  });

  it("marks single and multiple highlights", () => {
    expect(splitHighlights("la <b>bolletta</b> della <b>luce</b>")).toEqual([
      { text: "la ", highlighted: false },
      { text: "bolletta", highlighted: true },
      { text: " della ", highlighted: false },
      { text: "luce", highlighted: true },
    ]);
  });

  it("does not interpret other HTML", () => {
    expect(splitHighlights("<script>x</script>")).toEqual([{ text: "<script>x</script>", highlighted: false }]);
  });

  it("drops empty segments", () => {
    expect(splitHighlights("<b>solo</b>")).toEqual([{ text: "solo", highlighted: true }]);
  });
});
