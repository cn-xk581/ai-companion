// 关系阶段 / 情绪 的中文与表情映射（与后端 persona.models 保持一致）
export const STAGE_LABELS = {
  stranger: '陌生人',
  acquaintance: '认识',
  crush: '暧昧',
  dating: '恋爱',
  couple: '情侣',
}

export const STAGE_ORDER = ['stranger', 'acquaintance', 'crush', 'dating', 'couple']

export const MOOD_LABELS = {
  happy: '开心',
  neutral: '平静',
  sad: '难过',
  angry: '生气',
  shy: '害羞',
  excited: '兴奋',
  tired: '疲惫',
}

export const MOOD_EMOJI = {
  happy: '😊',
  neutral: '😐',
  sad: '😢',
  angry: '😠',
  shy: '😳',
  excited: '🤩',
  tired: '😪',
}

// 常用表情（输入框表情面板）
export const EMOJIS = [
  '😀', '😄', '😁', '😊', '🥰', '😍', '😘', '😉',
  '😭', '😅', '🤣', '😂', '😳', '🙄', '😴', '🤔',
  '❤️', '💕', '🌹', '💋', '💘', '💯', '✨', '🎉',
  '🐱', '🐶', '🌸', '🌙', '⭐', '☕', '🍵', '🍰',
]

export const PERSONA_FIELDS = [
  { key: 'name', label: '名字', type: 'text' },
  { key: 'age', label: '年龄', type: 'number' },
  { key: 'personality', label: '性格', type: 'textarea' },
  { key: 'speaking_style', label: '说话风格', type: 'textarea' },
  { key: 'background', label: '背景故事', type: 'textarea' },
]
