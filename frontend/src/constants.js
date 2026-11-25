// 伴侣域文案：与 app/domain/companion.py 保持一致

export const STAGE_LABELS = {
  stranger: '陌生人',
  acquaintance: '认识',
  crush: '暧昧',
  dating: '恋爱',
  couple: '情侣',
}

export const STAGE_PHRASES = {
  stranger: '还不认识',
  acquaintance: '刚认识',
  crush: '暧昧中',
  dating: '在恋爱',
  couple: '在一起',
}

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

export const EMOJIS = [
  '😀', '😄', '😊', '🥰', '😍', '😘', '😉', '😭',
  '😅', '🤣', '😂', '😳', '🙄', '😴', '🤔', '😤',
  '❤️', '💕', '🫶', '🌹', '💋', '✨', '🎉', '🌙',
  '🐱', '🐶', '🌸', '⭐', '☕', '🍵', '🍰', '🫂',
]

export const TURN_STATUS = {
  done: '已完成',
  awaiting_review: '待复核',
  blocked: '已拦截',
  failed: '失败',
  running: '进行中',
}

export const REVIEW_CATEGORY_LABELS = {
  self_harm: '自伤风险',
  stage_mismatch_output: '尺度越界',
  out_of_character: '出戏',
  too_long: '过长',
}

export const PERSONA_FIELDS = [
  { key: 'name', label: '名字', type: 'text', placeholder: '小柔' },
  { key: 'age', label: '年龄', type: 'number', placeholder: '22' },
  { key: 'personality', label: '性格', type: 'textarea', placeholder: '温柔、有点小傲娇…' },
  { key: 'speaking_style', label: '说话风格', type: 'textarea', placeholder: '口语化，喜欢用语气词' },
  { key: 'background', label: '背景', type: 'textarea', placeholder: '都市白领，喜欢猫和深夜电影' },
]

export function stageLabel(stage) {
  return STAGE_LABELS[stage] || stage || ''
}
export function stagePhrase(stage) {
  return STAGE_PHRASES[stage] || stageLabel(stage)
}
export function moodLabel(mood) {
  return MOOD_LABELS[mood] || mood || ''
}
export function moodEmoji(mood) {
  return MOOD_EMOJI[mood] || ''
}
