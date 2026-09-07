"""accounts：注册部门策略测试（安全回归）。"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from kb.models import KnowledgeBase

from .models import DEPARTMENT_GENERAL, UserProfile


class RegistrationDepartmentPolicyTests(TestCase):
    def test_register_forces_general_department(self):
        """注册不受理部门自选：伪造 department=机械 也必须落「通用」。"""
        KnowledgeBase.objects.create(
            name="机械库", slug="mech-lib", is_folder=True,
            created_by=get_user_model().objects.create_user("seed", password="x"),
            department="机械",
        )
        r = self.client.post(reverse("accounts:register"), {
            "username": "newbie",
            "password1": "s3cure-pass-9",
            "password2": "s3cure-pass-9",
            "department": "机械",  # 恶意/越权自选
        })
        self.assertEqual(r.status_code, 302)
        profile = UserProfile.objects.get(user__username="newbie")
        self.assertEqual(profile.department, DEPARTMENT_GENERAL)

    def test_register_page_does_not_leak_department_list(self):
        """注册页不再向匿名用户暴露内部部门名单。"""
        KnowledgeBase.objects.create(
            name="机械库", slug="mech-lib2", is_folder=True,
            created_by=get_user_model().objects.create_user("seed2", password="x"),
            department="机械",
        )
        r = self.client.get(reverse("accounts:register"))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "机械")
        self.assertNotContains(r, 'name="department"')
