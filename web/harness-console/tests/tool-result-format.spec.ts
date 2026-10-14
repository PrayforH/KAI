import { expect,it } from "vitest";
import { decodeToolResult, formatToolResult, queryResultTable } from "../src/lib/tool-result-format";
import { toolActivitySentence, toolArgumentFacts } from "../src/lib/tool-presentation";
it("unwraps double encoded MCP text into query rows without changing values",()=>{
 const data={engine:"dameng",columns:["类型","数量"],rows:[["OTHER",9809],[null,0]],returned_rows:2,truncated:false};
 const wrapped=JSON.stringify({content:[{type:"text",text:JSON.stringify(JSON.stringify(data))}]});
 expect(decodeToolResult(JSON.stringify([{type:"text",text:JSON.stringify(data)}]))).toEqual(data);
 expect(decodeToolResult(wrapped)).toEqual(data);expect(queryResultTable(wrapped)?.rows).toEqual(data.rows);
 expect(formatToolResult(wrapped)).toBe(JSON.stringify(data,null,2));
});
it("keeps malformed and ordinary strings intact and prefers structured output",()=>{
 expect(formatToolResult('{broken}')).toBe('{broken}');expect(formatToolResult('line one\nline two')).toBe('line one\nline two');
 expect(decodeToolResult({structuredContent:{ok:true},content:[{type:"text",text:"preview"}]})).toEqual({ok:true});
 expect(queryResultTable('{"columns":["a"],"rows":[[1,2]]}')).toBeNull();
});
it("uses a readable database operation instead of opaque IDs and credentials",()=>{
 expect(toolActivitySentence({name:"mcp__database__query",status:"completed",arguments:{source_id:"connection-secret-id",sql:"SELECT 1"}})).toBe("已查询数据库");
 expect(toolArgumentFacts({name:"unknown",arguments:{password:"private",token:"private",host:"db"}})).toEqual(["host db"]);
});
