import { useState, useEffect } from 'react';
import { OcrBlock, PaddleOcrBlocks } from '../types/project';

interface OcrDocumentViewProps {
  blocks: PaddleOcrBlocks | undefined | null;
  imageUrl: string | null;
}

// Visual block types that should show cropped images
const VISUAL_BLOCK_TYPES = ['figure', 'chart', 'seal', 'stamp', 'image'];

// Process text to convert footnote markers like $^{2}$ or $ ^{2} $ to superscript
const processFootnotes = (text: string): React.ReactNode => {
  if (!text) return text;

  // Match patterns like $^{n}$ or $ ^{n} $ where n is a number
  const pattern = /\$\s*\^\s*\{(\d+)\}\s*\$/g;

  if (!pattern.test(text)) {
    return text;
  }

  // Reset regex lastIndex after test
  pattern.lastIndex = 0;

  const result: React.ReactNode[] = [];
  let lastIndex = 0;
  let match;
  let keyIndex = 0;

  while ((match = pattern.exec(text)) !== null) {
    // Add text before the match
    if (match.index > lastIndex) {
      result.push(text.slice(lastIndex, match.index));
    }

    // Add the superscript
    result.push(
      <sup
        key={keyIndex++}
        className="text-xs font-medium"
        style={{ color: 'var(--color-blue-500)' }}
      >
        {match[1]}
      </sup>,
    );

    lastIndex = match.index + match[0].length;
  }

  // Add remaining text
  if (lastIndex < text.length) {
    result.push(text.slice(lastIndex));
  }

  return result;
};

export default function OcrDocumentView({
  blocks,
  imageUrl,
}: OcrDocumentViewProps) {
  const [imageDimensions, setImageDimensions] = useState<{
    width: number;
    height: number;
  } | null>(null);
  const [imageLoaded, setImageLoaded] = useState(false);

  // Load image to get natural dimensions for CSS-based cropping
  useEffect(() => {
    if (!imageUrl) {
      setImageLoaded(true);
      return;
    }

    const img = new Image();
    img.onload = () => {
      setImageDimensions({
        width: img.naturalWidth,
        height: img.naturalHeight,
      });
      setImageLoaded(true);
    };
    img.onerror = () => {
      console.warn('Failed to load image');
      setImageLoaded(true);
    };
    img.src = imageUrl;
  }, [imageUrl]);

  // Render a single block
  const renderBlock = (block: OcrBlock) => {
    const isVisualBlock = VISUAL_BLOCK_TYPES.includes(block.block_label);

    // Skip empty non-visual blocks
    if (!isVisualBlock && !block.block_content?.trim()) {
      return null;
    }

    // Render visual blocks with CSS-based cropping
    if (isVisualBlock && imageUrl && block.block_bbox?.length === 4) {
      const [x1, y1, x2, y2] = block.block_bbox;
      const cropWidth = x2 - x1;
      const cropHeight = y2 - y1;

      // Use blocks dimensions or fallback to image dimensions
      const sourceWidth = blocks?.width || imageDimensions?.width || 1;
      const sourceHeight = blocks?.height || imageDimensions?.height || 1;

      // Calculate display size (max 400px width)
      const displayWidth = Math.min(cropWidth, 400);
      const scale = displayWidth / cropWidth;
      const displayHeight = cropHeight * scale;

      // Scale factor for background positioning
      const bgScale = displayWidth / cropWidth;
      const bgWidth = sourceWidth * bgScale;
      const bgHeight = sourceHeight * bgScale;
      const bgX = -x1 * bgScale;
      const bgY = -y1 * bgScale;

      return (
        <div key={block.block_id} className="ocr-block ocr-block-visual my-4">
          <div
            className="rounded-lg shadow-md overflow-hidden"
            style={{
              width: displayWidth,
              height: displayHeight,
              backgroundImage: `url(${imageUrl})`,
              backgroundSize: `${bgWidth}px ${bgHeight}px`,
              backgroundPosition: `${bgX}px ${bgY}px`,
              backgroundRepeat: 'no-repeat',
            }}
          />
          {block.block_content?.trim() && (
            <p className="text-sm text-slate-500 italic mt-2">
              {block.block_content}
            </p>
          )}
        </div>
      );
    }

    // Fallback for visual blocks without valid bbox - show full image
    if (isVisualBlock) {
      return (
        <div key={block.block_id} className="ocr-block ocr-block-visual my-4">
          {imageUrl ? (
            <img
              src={imageUrl}
              alt={block.block_label}
              className="max-w-full h-auto rounded-lg shadow-md"
              style={{ maxHeight: 300 }}
            />
          ) : (
            <div className="flex items-center justify-center h-32 bg-slate-100 dark:bg-slate-800 rounded-lg">
              <span className="text-sm text-slate-400">
                [{block.block_label.toUpperCase()}]
              </span>
            </div>
          )}
          {block.block_content?.trim() && (
            <p className="text-sm text-slate-500 italic mt-2">
              {block.block_content}
            </p>
          )}
        </div>
      );
    }

    // Render text blocks by type
    switch (block.block_label) {
      case 'doc_title':
        return (
          <h1
            key={block.block_id}
            className="text-2xl font-bold text-slate-900 dark:text-white mt-6 mb-4"
          >
            {block.block_content}
          </h1>
        );

      case 'paragraph_title':
        return (
          <h2
            key={block.block_id}
            className="text-xl font-semibold text-slate-800 dark:text-slate-100 mt-5 mb-3"
          >
            {block.block_content}
          </h2>
        );

      case 'header':
        return (
          <div
            key={block.block_id}
            className="text-sm font-medium text-slate-600 dark:text-slate-400 mb-2"
          >
            {block.block_content}
          </div>
        );

      case 'footer':
        return (
          <div
            key={block.block_id}
            className="text-xs text-slate-400 dark:text-slate-500 mt-4 pt-2 border-t border-slate-200 dark:border-slate-700"
          >
            {block.block_content}
          </div>
        );

      case 'table':
        return (
          <div
            key={block.block_id}
            className="ocr-block ocr-block-table my-4 overflow-x-auto"
          >
            <div
              className="prose prose-sm max-w-none prose-table:border-collapse prose-th:border prose-th:border-slate-300 prose-th:bg-slate-100 prose-th:p-2 prose-td:border prose-td:border-slate-300 prose-td:p-2 dark:prose-th:border-slate-600 dark:prose-th:bg-slate-800 dark:prose-td:border-slate-600"
              dangerouslySetInnerHTML={{ __html: block.block_content }}
            />
          </div>
        );

      case 'footnote':
        return (
          <div
            key={block.block_id}
            className="ocr-block-footnote my-2 pl-4 border-l-2 border-slate-200 dark:border-slate-700"
          >
            <p className="text-xs text-slate-500 dark:text-slate-400 leading-relaxed">
              {processFootnotes(block.block_content)}
            </p>
          </div>
        );

      case 'text':
      default:
        return (
          <p
            key={block.block_id}
            className="text-sm text-slate-700 dark:text-slate-300 leading-relaxed mb-3"
          >
            {processFootnotes(block.block_content)}
          </p>
        );
    }
  };

  if (!blocks?.blocks || blocks.blocks.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-slate-400">
        <svg
          className="h-12 w-12 mb-3"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={1.5}
            d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
          />
        </svg>
        <p className="text-sm font-medium">No document blocks available</p>
      </div>
    );
  }

  // Sort blocks by block_order if available
  const sortedBlocks = [...blocks.blocks].sort((a, b) => {
    const orderA = a.block_order ?? a.block_id;
    const orderB = b.block_order ?? b.block_id;
    return orderA - orderB;
  });

  return (
    <div className="ocr-document-view">
      {!imageLoaded && imageUrl && (
        <div className="flex items-center justify-center py-8">
          <svg
            className="h-6 w-6 text-slate-400 animate-spin"
            fill="none"
            viewBox="0 0 24 24"
          >
            <circle
              className="opacity-25"
              cx="12"
              cy="12"
              r="10"
              stroke="currentColor"
              strokeWidth="4"
            />
            <path
              className="opacity-75"
              fill="currentColor"
              d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
            />
          </svg>
          <span className="ml-2 text-sm text-slate-500">Loading images...</span>
        </div>
      )}
      <div className={imageLoaded || !imageUrl ? 'block' : 'hidden'}>
        {sortedBlocks.map((block) => renderBlock(block))}
      </div>
    </div>
  );
}
