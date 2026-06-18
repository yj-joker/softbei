import { request } from './request'

export const draftFromTask = (taskId) =>
  request({ url: `/weixiu/case-record/draft-from-task/${taskId}`, method: 'POST', throwOnError: true })

// 从上传材料(文件/图片/文字/语音转写)起草草稿，data 为 FormData
export const draftFromUpload = (formData) =>
  request({ url: '/weixiu/case-record/draft-from-upload', method: 'POST', data: formData, throwOnError: true })

export const submitCase = (data) =>
  request({ url: '/weixiu/case-record/submit', method: 'POST', data, throwOnError: true })

export const getPendingCases = (page = 1, size = 10) =>
  request({ url: '/weixiu/case-record/pending', method: 'GET', params: { page, size } })

export const approveCase = (id, data) =>
  request({ url: `/weixiu/case-record/${id}/approve`, method: 'POST', data, throwOnError: true })

export const rejectCase = (id, comment) =>
  request({ url: `/weixiu/case-record/${id}/reject`, method: 'POST', params: { comment }, throwOnError: true })

export const getMyCases = (page = 1, size = 10) =>
  request({ url: '/weixiu/case-record/mine', method: 'GET', params: { page, size } })
