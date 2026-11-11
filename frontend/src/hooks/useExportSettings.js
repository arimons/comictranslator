import { useEffect, useState } from "react";

// 내보내기 탭의 이미지 생성(AI Recovery) 설정, 생성 후보 선택, 미리보기 페이지, 내보내기 로그.
// App.jsx에서 그대로 옮겼다. 편집기의 AI로 통번역도 이 설정(모델·품질 등)을 따른다.
// 제공자 맞춤 effect는 원래 App의 시작 effect 뒤에 있었으나, 이 값들을 읽는 다른 effect가 없어 훅 자리에서 돌아도 결과가 같다.
export default function useExportSettings({ ocrProvider, effectiveOcrModel, imageModels }) {
  const [exportLog, setExportLog] = useState("");
  const [exportImageModel, setExportImageModel] = useState("gpt-image-2.5-sunburst");
  const [exportTextModel, setExportTextModel] = useState("gpt-6.1-terra");
  const [exportImageQuality, setExportImageQuality] = useState("medium");
  const [exportImageSize, setExportImageSize] = useState("match_input");
  const [exportOutputFormat, setExportOutputFormat] = useState("jpeg");
  const [exportOutputCompression, setExportOutputCompression] = useState(92);
  const [exportPromptGuide, setExportPromptGuide] = useState("");
  const [exportAnalyzeFirst, setExportAnalyzeFirst] = useState(true);
  const [exportGenerateIds, setExportGenerateIds] = useState([]);
  const [exportFocusedId, setExportFocusedId] = useState("");

  // OCR·번역 제공자를 바꾸면 통번역 재생성 모델도 같은 제공자로 맞춘다(입력란에서 직접 고칠 수는 있다)
  useEffect(() => {
    const gemini = ocrProvider === "gemini";
    setExportImageModel((gemini ? imageModels.gemini : imageModels.openai) || "gpt-image-2.5-sunburst");
    setExportTextModel(gemini ? effectiveOcrModel : "gpt-6.1-terra");
  }, [ocrProvider, effectiveOcrModel, imageModels]);

  function toggleExportGenerateId(imageId) {
    setExportGenerateIds((current) => (
      current.includes(imageId)
        ? current.filter((id) => id !== imageId)
        : [...current, imageId]
    ));
  }

  return {
    exportLog, setExportLog, exportImageModel, setExportImageModel, exportTextModel, setExportTextModel,
    exportImageQuality, setExportImageQuality, exportImageSize, setExportImageSize,
    exportOutputFormat, setExportOutputFormat, exportOutputCompression, setExportOutputCompression,
    exportPromptGuide, setExportPromptGuide, exportAnalyzeFirst, setExportAnalyzeFirst,
    exportGenerateIds, setExportGenerateIds, exportFocusedId, setExportFocusedId, toggleExportGenerateId,
  };
}
