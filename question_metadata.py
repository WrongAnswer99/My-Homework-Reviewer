"""Shared display text for grading metadata saved with a question."""
import re


def graded_answer_note(question, markdown=False):
    status = {'correct': '正确', 'incorrect': '错误', 'partial': '部分正确'}.get(
        question.get('grading'))
    if status is None or 'student_answer' not in question:
        return ''
    answer = question['student_answer'] or '未作答'
    if markdown:
        answer = re.sub(r'([\\`*\[\]<>_])', r'\\\1', answer)
    return f'我的答案：{answer}，该题{status}。'
