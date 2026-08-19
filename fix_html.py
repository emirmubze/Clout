#!/usr/bin/env python3
import re

with open('shop/templates/shop/course_detail.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix the broken HTML structure - the closing tags were removed
pattern = r'(<button class="buy-btn">Buy now</button>)\s+([^\n]*COURSE CONTENT)'
replacement = r'''\1
      </div>
    </div>
  </section>

  <!-- =========================================================
       \2'''

content = re.sub(pattern, replacement, content, flags=re.MULTILINE)

with open('shop/templates/shop/course_detail.html', 'w', encoding='utf-8') as f:
    f.write(content)

print("HTML structure fixed!")
