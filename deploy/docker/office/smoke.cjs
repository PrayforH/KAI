const assert = require("node:assert/strict");
const { Document, Packer, Paragraph } = require("docx");
const PptxGenJS = require("pptxgenjs");
const { imageSize } = require("image-size");

async function main() {
  const png = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a6XcAAAAASUVORK5CYII=",
    "base64",
  );
  const dimensions = imageSize(png);
  assert.equal(dimensions.width, 1);
  assert.equal(dimensions.height, 1);
  const presentation = new PptxGenJS();
  presentation.addSlide().addImage({
    data: `data:image/png;base64,${png.toString("base64")}`,
    x: 1, y: 1, w: 2, h: 2,
  });
  const slides = await presentation.write({ outputType: "nodebuffer" });
  const document = await Packer.toBuffer(new Document({
    sections: [{ children: [new Paragraph("Office runtime smoke")] }],
  }));
  for (const output of [slides, document]) {
    assert.ok(Buffer.isBuffer(output));
    assert.ok(output.length > 1000);
    assert.equal(output.subarray(0, 2).toString(), "PK");
  }
  console.log("Office runtime: PNG dimensions, PPTX with image, and DOCX passed");
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
