import json
from django.core.urlresolvers import reverse
from django_webtest import WebTest

from facile_backlog.backlog.models import Backlog, Event, Project

from . import factories


class BacklogTest(WebTest):
    def test_backlog_list(self):
        user = factories.UserFactory.create(
            email='test@epyx.ch', password='pass')
        backlog = factories.create_sample_backlog(user)
        url = reverse("project_detail", args=(backlog.project.pk,))
        self.app.get(url, status=302)
        response = self.app.get(url, user=user)
        self.assertContains(response, backlog.name)

    def test_project_backlog_create(self):
        user = factories.UserFactory.create(
            email='test@epyx.ch', password='pass')
        project = factories.create_sample_project(user)

        url = reverse('project_backlog_create', args=(project.pk,))
        # login redirect
        self.app.get(url, status=302)
        response = self.app.get(url, user=user)
        self.assertContains(response, u"Add a new backlog")

        form = response.forms['edit_backlog_form']
        for key, value in {
            'name': 'Backlog one',
            'description': 'Description one',
        }.iteritems():
            form[key] = value
        response = form.submit().follow()
        self.assertContains(response, u"Backlog successfully created.")
        self.assertContains(response, u"Backlog one")
        backlog = Backlog.objects.get()
        self.assertTrue(Backlog.objects.exists())
        event = Event.objects.get(
            project=project,
            backlog=backlog
        )
        self.assertEqual(event.text, "created this backlog")
        self.assertEqual(event.user, user)

    def test_backlog_edit(self):
        user = factories.UserFactory.create(
            email='test@epyx.ch', password='pass')
        backlog = factories.create_sample_backlog(user, backlog_kwargs={
            'name': "Backlog 1",
            'description': "Description 1",
        })
        url = reverse('project_backlog_edit', args=(
            backlog.project.pk, backlog.pk))
        # login redirect
        self.app.get(url, status=302)
        response = self.app.get(url, user=user)
        self.assertContains(response, u"Edit backlog")

        form = response.forms['edit_backlog_form']
        for key, value in {
            'name': 'New name',
            'description': 'New Description',
        }.iteritems():
            form[key] = value
        response = form.submit().follow()
        self.assertContains(response, u"Backlog successfully updated.")
        self.assertContains(response, u"New name")
        project = Backlog.objects.get(pk=backlog.pk)
        self.assertEqual(project.name, "New name")
        self.assertEqual(project.description, "New Description")
        event = Event.objects.get(
            project=project,
            backlog=backlog
        )
        self.assertEqual(event.text, "modified the backlog")
        self.assertEqual(event.user, user)

    def test_backlog_delete(self):
        user = factories.UserFactory.create(
            email='test@epyx.ch', password='pass')
        backlog = factories.create_sample_backlog(
            user, backlog_kwargs={
                'name': "My backlog"
            }
        )
        url = reverse('project_backlog_delete', args=(
            backlog.project.pk, backlog.pk))
        # login redirect
        self.app.get(url, status=302)
        response = self.app.get(url, user=user)
        self.assertContains(response, u"Delete backlog")
        form = response.forms['delete_form']
        response = form.submit().follow()
        self.assertContains(response, u"Backlog successfully deleted.")
        self.assertFalse(Backlog.objects.filter(pk=backlog.pk).exists())
        event = Event.objects.get(
            project=backlog.project,
        )
        self.assertEqual(event.text, "deleted backlog My backlog")
        self.assertEqual(event.user, user)

    def test_security(self):
        user_1 = factories.UserFactory.create()
        user_2 = factories.UserFactory.create()

        backlog = factories.create_sample_backlog(user_1)
        project = backlog.project
        url = reverse('project_backlog_edit', args=(project.pk, backlog.pk))
        self.app.get(url, user=user_1)
        self.app.get(url, user=user_2, status=404)

        self.assertFalse(backlog.can_read(user_2))
        self.assertFalse(backlog.can_admin(user_2))
        project.add_user(user_2, is_admin=False)

        backlog = Backlog.objects.get(pk=backlog.pk)
        self.assertTrue(backlog.can_read(user_2))
        self.assertFalse(backlog.can_admin(user_2))

        # make user 2 admin
        project.add_user(user_2, is_admin=True)

        backlog = Backlog.objects.get(pk=backlog.pk)
        self.assertTrue(backlog.can_read(user_2))
        self.assertTrue(backlog.can_admin(user_2))

        self.assertEqual(project.users.count(), 2)

        self.app.get(url, user=user_2)

        project.remove_user(user_1)
        self.app.get(url, user=user_1, status=404)


class AjaxTest(WebTest):
    csrf_checks = False

    def test_backlog_reorder(self):
        user = factories.UserFactory.create(
            email='test@test.ch', password='pass')
        wrong_user = factories.UserFactory.create(
            email='wrong@test.ch', password='pass')
        project = factories.create_sample_project(user)
        for i in range(1, 4):
            factories.BacklogFactory.create(
                project=project,
                order=i,
            )

        order = [c.pk for c in project.backlogs.all()]
        order.reverse()
        url = reverse('api_move_backlog', args=(project.pk,))
        data = json.dumps({
            'order': order,
        })
        # if no write access, returns a 404
        self.app.post(url, data, status=401)
        self.app.post(url, data, user=wrong_user, status=404)
        self.app.post(url, data,
                      content_type="application/json",
                      user=user)
        project = Project.objects.get(pk=project.pk)
        result_order = [c.pk for c in project.backlogs.all()]
        self.assertEqual(order, result_order)
