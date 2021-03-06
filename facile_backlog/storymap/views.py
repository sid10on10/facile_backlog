from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.urlresolvers import reverse
from django.db.models import Max
from django.http import Http404
from django.http.response import (HttpResponseForbidden)
from django.shortcuts import get_object_or_404, redirect
from django.template import RequestContext, loader
from django.utils.translation import ugettext_lazy as _
from django.views import generic


from rest_framework.response import Response
from rest_framework.decorators import (api_view, parser_classes)
from rest_framework.parsers import JSONParser

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction


from ..backlog.views import NoCacheMixin, ProjectMixin
from ..backlog.models import create_event
from ..api.notify import notify_changes
from ..util import get_websocket_url

from models import (StoryMap, Story, Theme, Phase)
from forms import StoryMapCreationForm, StoryMapEditForm

from excel import export_excel


class StoryMapMixin(NoCacheMixin):
    admin_only = False
    """
    Mixin to fetch a organization by a view.
    """
    def dispatch(self, request, *args, **kwargs):
        self.story_map = get_object_or_404(StoryMap,
                                           pk=kwargs['storymap_id'])

        if self.admin_only and not self.story_map.can_admin(request.user):
            if self.story_map.can_read(request.user):
                return HttpResponseForbidden(_("Not authorized"))
            else:
                raise Http404
        else:
            if not self.story_map.can_read(request.user):
                raise Http404

        self.request = request
        self.pre_dispatch()
        return super(StoryMapMixin, self).dispatch(request, *args, **kwargs)

    def pre_dispatch(self):
        pass


class StoryMapList(ProjectMixin, generic.ListView):
    template_name = "storymap/storymap_list.html"

    def get_queryset(self):
        return StoryMap.objects.filter(project=self.project)

    def get_context_data(self, **kwargs):
        context = super(StoryMapList, self).get_context_data(**kwargs)
        context['project'] = self.project
        return context
storymap_list = login_required(StoryMapList.as_view())


class StoryMapDetail(StoryMapMixin, generic.DetailView):
    template_name = "storymap/storymap_detail.html"

    def get_object(self):
        return self.story_map

    def get_context_data(self, **kwargs):
        context = super(StoryMapDetail, self).get_context_data(**kwargs)
        context['storymap'] = self.story_map
        context['stories'] = self.story_map.stories()
        context['project'] = self.story_map.project
        context['active'] = "story_map"
        context['themes'] = self.story_map.themes.all()
        context['phases'] = self.story_map.phases.all()
        context['story_colors'] = STORY_COLORS
        context['ws_url'] = get_websocket_url(self.request)
        return context
storymap_detail = login_required(StoryMapDetail.as_view())


class StoryMapCreate(ProjectMixin, generic.CreateView):
    admin_only = True
    template_name = "storymap/storymap_form.html"
    form_class = StoryMapCreationForm

    def get_form_kwargs(self):
        kwargs = super(StoryMapCreate, self).get_form_kwargs()
        kwargs['project'] = self.project
        return kwargs

    def get_context_data(self, **kwargs):
        context = super(StoryMapCreate, self).get_context_data(**kwargs)
        context['project'] = self.project
        return context

    def form_valid(self, form):
        super(StoryMapCreate, self).form_valid(form)
        Phase.objects.create(
            name=_("First line"),
            story_map=self.object
        )
        Theme.objects.create(
            name=_("First column"),
            story_map=self.object
        )
        create_event(self.request.user, _("created a board"),
                     project=self.project)
        messages.success(self.request,
                         _("Board successfully created."))
        return redirect(reverse("storymap_detail", args=(self.object.pk,)))
storymap_create = login_required(StoryMapCreate.as_view())


class StoryMapEdit(StoryMapMixin, generic.UpdateView):
    admin_only = True
    template_name = "storymap/storymap_form.html"
    form_class = StoryMapEditForm

    def get_object(self, queryset=None):
        return self.story_map

    def get_context_data(self, **kwargs):
        context = super(StoryMapEdit, self).get_context_data(**kwargs)
        context['project'] = self.story_map.project
        return context

    def form_valid(self, form):
        super(StoryMapEdit, self).form_valid(form)
        create_event(self.request.user, _("edited a board"),
                     project=self.story_map.project_id)
        messages.success(self.request,
                         _("Board successfully modified."))
        return redirect(reverse("storymap_list",
                                args=(self.object.project_id,)))
storymap_edit = login_required(StoryMapEdit.as_view())


class StoryMapDelete(StoryMapMixin, generic.DeleteView):
    admin_only = True
    template_name = "storymap/storymap_confirm_delete.html"

    def get_object(self):
        return self.story_map

    def delete(self, request, *args, **kwargs):
        self.story_map.delete()
        create_event(self.request.user, _("deleted a board"),
                     project=self.story_map.project_id)
        messages.success(request,
                         _("Board successfully deleted."))
        return redirect(reverse("storymap_list",
                                args=(self.story_map.project_id,)))
storymap_delete = login_required(StoryMapDelete.as_view())


def get_or_errors(dic, value, errors=[]):
    if value not in dic:
        errors.append("Missing value '{0}' in content".format(value))
        return None
    return dic.get(value)


STORY_COLORS = (
    "#ffc",
    "#cff",
    "#cdf",
    "#aaf",
    "#fcf",
    "#fcd",
    "#fbb",
    "#dfd",
    "#afa",
    "#ffdead",
    "#ddd",
    "#fff",
)
TARGETS = {
    'story': (Story, False, "storymap/_story_cell.html"),
    'theme': (Theme, True, "storymap/_theme_col.html"),
    'phase': (Phase, True, "storymap/_phase_row.html"),
}

CREATE = "create"
DELETE = "delete"
UPDATE = "update"
ORDER = "order"


@api_view(["POST"])
@parser_classes((JSONParser,))
@transaction.commit_on_success
def story_map_action(request, story_map_id):
    story_map = get_object_or_404(StoryMap, pk=story_map_id)
    if not story_map.can_admin(request.user):
        if story_map.can_read(request.user):
            return Response("You are not admin of this story map", status=403)
        # verify access rights on story project
        raise Http404
    errors = []
    target = get_or_errors(request.DATA, 'target', errors)
    action = get_or_errors(request.DATA, 'action', errors)
    content_ori = request.DATA.get('content', dict())
    content = dict(content_ori)
    target_id = request.DATA.get('id', None)
    model_class = TARGETS[target][0]
    html = None
    if TARGETS[target][1]:
        content['story_map'] = story_map
    if errors:
        return Response({
            'errors': errors
        }, content_type="application/json", status=400)
    try:
        if action == CREATE:
            # should place it at the end ( order == max(order) )
            max_filter = {
                k: content[k]
                for k in ('theme_id', 'phase_id', 'story_map')
                if k in content
            }
            max_order = model_class.objects.filter(
                **max_filter).aggregate(Max('order'))
            if max_order['order__max'] is not None:
                content['order'] = max_order['order__max'] + 1
            else:
                content['order'] = 0
            obj = model_class.objects.create(**content)
            target_id = obj.pk
            template_name = TARGETS[target][2]
            if template_name:
                t = loader.get_template(template_name)
                c = RequestContext(request, {
                    'object': obj,
                    'storymap': story_map,
                    'themes': story_map.themes,
                    'phases': story_map.phases,
                    'story_colors': STORY_COLORS,
                })
                html = t.render(c)
            event_text = _("created story map's '%s'") % (target,)
        elif action == UPDATE:
            order = content.pop('order', None)
            obj = model_class.objects.get(pk=target_id)
            for k, v in content.items():
                setattr(obj, k, v)
            obj.save()
            event_text = _("updated story map's '%s'") % (target,)
            if order:
                order = [int(x) for x in order]
                for item in model_class.objects.filter(pk__in=order).all():
                    index = order.index(item.pk)
                    if item.order != index:
                        item.order = index
                        item.save()
                event_text = _("re-order story map's '%s'") % (target,)
        elif action == DELETE:
            obj = model_class.objects.get(pk=target_id)
            obj.delete()
            event_text = _("deleted story map's '%s'") % (target,)
        elif action == ORDER:
            order = [int(x) for x in content['order']]
            for item in model_class.objects.filter(pk__in=order).all():
                index = order.index(item.pk)
                if item.order != index:
                    item.order = index
                    item.save()
            event_text = _("re-order story map's '%s'") % (target,)
        else:
            return Response({
                'errors': [
                    'Unknown commend: {0}'.format(action)
                ]
            }, content_type="application/json", status=400)
    except ObjectDoesNotExist:
        return Response({
            'errors': [
                'Unable to find {0} with id {1}'.format(target, target_id)
            ]
        }, content_type="application/json", status=400)
    create_event(request.user, event_text, project=story_map.project)
    notify_changes("storymap", story_map.pk, {
        'type': "storymap_update",
        'target': target,
        'id': target_id,
        'action': action,
        'content': content_ori
    })
    return Response({
        'id': target_id,
        'html': html,
        'ok': True
    }, content_type="application/json", status=200)


def get_story_map_element(request, map_pk, element_type, element_pk):
    story_map = get_object_or_404(StoryMap, pk=map_pk)
    if not story_map.can_read(request.user):
        raise Http404
    model_class = TARGETS[element_type][0]
    try:
        obj = model_class.objects.get(pk=element_pk)
        template_name = TARGETS[element_type][2]
        t = loader.get_template(template_name)
        c = RequestContext(request, {
            'object': obj,
            'storymap': story_map,
            'themes': story_map.themes,
            'phases': story_map.phases,
            'story_colors': STORY_COLORS,
        })
        html = t.render(c)
    except Story.DoesNotExist:
        return Response({
            'errors': [
                'Unable to find {0} with id {1}'.format(element_type,
                                                        element_pk)
            ]
        }, content_type="application/json", status=400)
    return Response({
        'id': element_pk,
        'phase_id': getattr(obj, "phase_id", None),
        'theme_id': getattr(obj, "theme_id", None),
        'html': html,
    }, content_type="application/json", status=200)


@api_view(["GET"])
def story_map_story(request, story_map_id):
    story_id = request.GET.get("story_id", 0)
    return get_story_map_element(request, story_map_id, "story", story_id)


@api_view(["GET"])
def story_map_phase(request, story_map_id):
    phase_id = request.GET.get("phase_id", 0)
    return get_story_map_element(request, story_map_id, "phase", phase_id)


@api_view(["GET"])
def story_map_theme(request, story_map_id):
    theme_id = request.GET.get("theme_id", 0)
    return get_story_map_element(request, story_map_id, "theme", theme_id)


class ExportBaord(StoryMapMixin, generic.TemplateView):
    template_name = "backlog/export_stories.html"

    def get(self, request, *args, **kwargs):
        name = u"Backlogman-board-{0}-{1}".format(
            self.story_map.project.name, self.story_map.name
        )
        title = u"Backlogman: {0} - {1}".format(
            self.story_map.project.name,
            self.story_map.name
        )
        return export_excel(self.story_map, name, title)
export_board = login_required(ExportBaord.as_view())
