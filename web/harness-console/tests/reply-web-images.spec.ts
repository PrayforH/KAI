import { describe, expect, it } from "vitest";
import { imageReferences, publicImageUrl, replyWebImages } from "../src/lib/reply-web-images";
import { runActivitySchema } from "../src/lib/activity-schema";
const photo = { url: "https://cdn.example.com/a.jpg", description: "街景", source_url: "https://news.example.com/story" };
const part = (result: unknown, toolName = "WebSearch") => ({ type: "tool-call", toolName, args: {query: "街景图片"}, result });

describe("turn-owned search photos", () => {
  it("unwraps native and SDK content blocks without interpreting prose as images", () => {
    const value = JSON.stringify({content:[{type:"text",text:JSON.stringify({images:[photo]})}]});
    expect(imageReferences(value)).toEqual([photo]);
    expect(imageReferences("图片 https://cdn.example.com/not-a-result.jpg")).toEqual([]);
    expect(replyWebImages([part(value)], undefined, "")).toEqual([{src:photo.url,title:photo.description,sourceUrl:photo.source_url}]);
  });
  it("restores bounded metadata from history, independently of a truncated tool preview", () => {
    const activity = runActivitySchema.parse({run_id:"r",status:"succeeded",started_at:"2026-10-22",items:[
      {id:"1",event_type:"tool.request",kind:"tool",status:"running",title:"查询",timestamp:"2026-10-22",sequence:1,metadata:{tool_call_id:"call",name:"mcp__harness-web__search",arguments:{query:"街景图片"}}},
      {id:"2",event_type:"tool.result",kind:"tool",status:"succeeded",title:"完成",timestamp:"2026-10-22",sequence:2,metadata:{tool_call_id:"call",result_preview:'{"results":[...truncated',web_images:[photo]}},
    ]});
    expect(replyWebImages([], activity, "")).toHaveLength(1);
    expect(replyWebImages([part({images:[photo]})], activity, `![已展示](${photo.url})`)).toEqual([]);
    activity.items[0].metadata.name = "Read";
    expect(replyWebImages([], activity, "")).toEqual([]);
  });
  it("leaves incidental search images out of text-only queries, failures and unrelated tools", () => {
    expect(replyWebImages([{...part({images:[photo]}),args:{query:"季度财报"}}], undefined, "")).toEqual([]);
    expect(replyWebImages([{...part({images:[photo]}),args:{query:"广州"}}], undefined, "", "请找广州街景照片")).toHaveLength(1);
    expect(replyWebImages([{...part({images:[photo]}),isError:true}], undefined, "")).toEqual([]);
    expect(replyWebImages([part({images:[photo]}, "Read")], undefined, "", "找图片")).toEqual([]);
  });
  it("deduplicates, caps the gallery and refuses credentials, unsafe protocols and private hosts", () => {
    const result = {images:[photo,photo,...Array.from({length:30},(_,i)=>({url:`https://cdn.example.com/${i}.png`}))]};
    const images = replyWebImages([part(result),part(result)], undefined, "");
    expect(images.length).toBeLessThanOrEqual(12);
    expect(new Set(images.map(image=>image.src)).size).toBe(images.length);
    for (const value of ["http://images.example.com/a", "https://user:pass@cdn.example.com/a", "https://127.0.0.1/a", "https://[::1]/a", "https://192.168.1.2/a", "https://169.254.169.254/a", "https://example.local/a", "data:image/png;base64,abc", "javascript:alert(1)"]) expect(publicImageUrl(value)).toBeUndefined();
    expect(publicImageUrl(photo.url+"#top")).toBe(photo.url);
  });
});
