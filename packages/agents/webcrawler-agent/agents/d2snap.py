"""
D2Snap: DOM Downsampling for Static Page Analysis

Compresses HTML content for efficient LLM analysis by:
- Removing unnecessary elements (scripts, styles, SVGs)
- Preserving interactive and content elements
- Limiting content size to token budget
"""

import logging
from typing import Literal

from bs4 import BeautifulSoup, Comment

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    """Estimate token count (rough approximation: 1 token ~ 4 chars)."""
    return len(text) // 4


# Preservation strategies for different analysis types
PRESERVATION_STRATEGIES = {
    'content_extraction': {
        'important_tags': {
            'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
            'p', 'article', 'section', 'main',
            'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
            'blockquote', 'pre', 'code',
            'a', 'img', 'figure', 'figcaption',
            'strong', 'em', 'b', 'i',
        },
        'keywords': ['article', 'content', 'main', 'post', 'entry', 'body', 'text'],
        'description': 'Preserves content elements for text extraction'
    },
    'browser_automation': {
        'important_tags': {
            'button', 'a', 'input', 'select', 'option', 'form',
            'nav', 'header', 'ul', 'li', 'label', 'div', 'span',
            'textarea', 'checkbox', 'radio',
        },
        'keywords': ['button', 'btn', 'search', 'sort', 'filter', 'cart', 'menu', 'nav', 'submit'],
        'description': 'Preserves interactive elements for browser automation'
    },
    'hybrid': {
        'important_tags': {
            # Content elements
            'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
            'p', 'article', 'section', 'main',
            'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'th', 'td',
            'blockquote', 'pre', 'code',
            'strong', 'em', 'b', 'i',
            # Interactive elements
            'button', 'a', 'input', 'select', 'option', 'form',
            'nav', 'header', 'label', 'textarea',
            # Media
            'img', 'figure', 'figcaption',
        },
        'keywords': ['content', 'main', 'article', 'nav', 'menu', 'button'],
        'description': 'Balanced preservation for content extraction with navigation context'
    },
}


class D2Snap:
    """
    D2Snap: DOM Downsampling for Static Page Analysis
    Optimized for web crawling and content extraction
    """

    @staticmethod
    def compress(
        html: str,
        max_tokens: int = 8000,
        analysis_type: Literal['content_extraction', 'browser_automation', 'hybrid'] = 'hybrid'
    ) -> dict:
        """
        Compress HTML for efficient LLM analysis.

        Args:
            html: Raw HTML content
            max_tokens: Target token budget
            analysis_type: Type of analysis to optimize for

        Returns:
            Dict with compressed HTML and statistics
        """
        original_tokens = estimate_tokens(html)
        logger.info(f"D2Snap ({analysis_type}): input ~{original_tokens} tokens, target {max_tokens}")

        strategy = PRESERVATION_STRATEGIES.get(analysis_type, PRESERVATION_STRATEGIES['hybrid'])
        important_tags = strategy['important_tags']

        soup = BeautifulSoup(html, 'lxml')

        # Remove non-content elements
        for tag in soup(['script', 'style', 'noscript', 'svg', 'iframe', 'link', 'meta', 'head']):
            tag.decompose()

        # Remove comments
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()

        # Remove hidden elements
        for tag in soup.find_all(attrs={'aria-hidden': 'true'}):
            tag.decompose()
        for tag in soup.find_all(attrs={'hidden': True}):
            tag.decompose()
        for tag in soup.find_all(style=lambda s: s and 'display:none' in s.replace(' ', '')):
            tag.decompose()

        # Keep important structural elements
        base_keep_tags = {'main', 'body', 'html'}
        keep_tags = base_keep_tags | important_tags

        # Simplify attributes for non-important tags
        attrs_to_keep = [
            'id', 'class', 'href', 'src', 'alt', 'title',
            'aria-label', 'role', 'type', 'name', 'placeholder', 'value',
            'data-testid', 'data-id',
        ]

        # If input is already small, skip aggressive compression
        if original_tokens < 500:
            logger.info(f"Input already small ({original_tokens} tokens), minimal compression only")
            compressed = str(soup)
            final_tokens = estimate_tokens(compressed)
            reduction = ((original_tokens - final_tokens) / original_tokens * 100) if original_tokens > 0 else 0
            return {
                'compressed_html': compressed,
                'original_tokens': original_tokens,
                'compressed_tokens': final_tokens,
                'reduction_percent': round(reduction, 1),
            }

        # Simplify attributes for non-important tags (but don't remove elements)
        for tag in soup.find_all(True):
            if tag.name not in keep_tags:
                tag.attrs = {k: v for k, v in tag.attrs.items() if k in attrs_to_keep}

        compressed = str(soup)
        current_tokens = estimate_tokens(compressed)
        logger.info(f"D2Snap after first pass: ~{current_tokens} tokens")

        # If still too large, apply more aggressive compression
        if current_tokens > max_tokens:
            soup = BeautifulSoup(compressed, 'lxml')

            # Truncate long text nodes
            for tag in soup.find_all(['p', 'div', 'span', 'li', 'td']):
                text = tag.get_text()
                if len(text) > 500:
                    # Keep first 400 chars
                    tag.string = text[:400] + "..."

            # Limit list items
            for ul in soup.find_all(['ul', 'ol']):
                items = ul.find_all('li', recursive=False)
                if len(items) > 10:
                    for item in items[10:]:
                        item.decompose()
                    # Add indicator
                    more_li = soup.new_tag('li')
                    more_li.string = f"... and {len(items) - 10} more items"
                    ul.append(more_li)

            # Limit table rows
            for tbody in soup.find_all('tbody'):
                rows = tbody.find_all('tr', recursive=False)
                if len(rows) > 20:
                    for row in rows[20:]:
                        row.decompose()

            compressed = str(soup)

        final_tokens = estimate_tokens(compressed)
        reduction = ((original_tokens - final_tokens) / original_tokens * 100) if original_tokens > 0 else 0
        logger.info(f"D2Snap complete: ~{final_tokens} tokens ({reduction:.1f}% reduction)")

        return {
            'compressed_html': compressed,
            'original_tokens': original_tokens,
            'compressed_tokens': final_tokens,
            'reduction_percent': round(reduction, 1),
        }

    @staticmethod
    def for_content_extraction(html: str, max_tokens: int = 8000) -> str:
        """Compress HTML optimized for content extraction."""
        result = D2Snap.compress(html, max_tokens, 'content_extraction')
        return result['compressed_html']

    @staticmethod
    def for_hybrid(html: str, max_tokens: int = 8000) -> str:
        """Compress HTML with balanced preservation for crawling."""
        result = D2Snap.compress(html, max_tokens, 'hybrid')
        return result['compressed_html']
