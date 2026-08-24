"""部门级知识库访问控制。

规则：
- 知识库 department == 「通用」 → 所有登录用户可见/可查。
- 否则仅 department 相同的用户（及 staff/superuser）可见。
- 文件夹与其子库部门保持同步（改文件夹时级联更新子库）。
"""
from __future__ import annotations

from django.db.models import Q, QuerySet

from .models import DEPARTMENT_GENERAL, Document, KnowledgeBase


def _user_dept(user) -> str:
    from accounts.models import user_department
    return user_department(user)


# 公开别名：views 等处直接 access.user_department(user)
def user_department(user) -> str:
    return _user_dept(user)


def kb_q(user) -> Q:
    """用户可见知识库的 Q 条件（通用 ∪ 本部门）。"""
    return Q(department=DEPARTMENT_GENERAL) | Q(department=_user_dept(user))


def kb_accessible(user, kb: KnowledgeBase) -> bool:
    """该用户能否访问此知识库（staff/superuser 全放行）。"""
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    dept = _user_dept(user)
    return kb.department in (DEPARTMENT_GENERAL, dept)


def accessible_kbs(user) -> QuerySet[KnowledgeBase]:
    """用户可见的顶层知识库（parent__isnull；子库对用户透明）。"""
    return KnowledgeBase.objects.filter(parent__isnull=True).filter(kb_q(user))


def accessible_doc_slugs(user) -> set[str]:
    """用户可检索的（子）库 slug 集合：可见顶层库自身 + 其全部子库。"""
    tops = list(accessible_kbs(user).values_list("slug", "id"))
    slugs = {s for s, _ in tops}
    child_rows = KnowledgeBase.objects.filter(
        parent_id__in=[i for _, i in tops], is_folder=False,
    ).values_list("slug", flat=True)
    slugs.update(child_rows)
    return slugs


def accessible_docs(user, completed_only: bool = True) -> QuerySet[Document]:
    """用户可见的文档（按所属库的部门过滤；子库与文件夹部门同步）。"""
    dept = _user_dept(user)
    qs = Document.objects.select_related("kb")
    if completed_only:
        qs = qs.filter(status=Document.Status.COMPLETED)
    return qs.filter(kb__department__in=[DEPARTMENT_GENERAL, dept])


def set_kb_department(kb: KnowledgeBase, department: str) -> None:
    """设置知识库部门；文件夹会级联更新其所有子库，保持扇出检索一致性。"""
    department = (department or "").strip() or DEPARTMENT_GENERAL
    kb.department = department
    kb.save(update_fields=["department", "updated_at"])
    if kb.is_folder:
        KnowledgeBase.objects.filter(parent=kb).update(department=department)
