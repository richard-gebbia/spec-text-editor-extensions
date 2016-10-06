import fileslices
import json
import sublime, sublime_plugin
import subprocess

# ==============================================================================
#  Data Transformations
# ==============================================================================

def deprecated_resource(uid):
    return {
        'uid': uid,
        'name': 'Deprecated',
        'description': 'Deprecated resource'
    }


def requirements_by_uid(json_requirements):
    uids_to_requirements = {}
    for requirement in json_requirements['requirements']:
        # Note: maybe warn against requirements that have the same uid
        #       (that should never happen)
        uids_to_requirements[requirement['uid']] = { 
            'uid': requirement['uid'],
            'name': requirement['name'],
            'description': requirement['description']
        }

    return uids_to_requirements


def resources_by_file(json_resources):
    files_to_resources = {}
    for resource in json_resources:
        # if a list for this file already exists, 
        # just append a new entry to the list
        if resource['uri']['file'] not in files_to_resources:
            files_to_resources[resource['uri']['file']] = []

        files_to_resources[resource['uri']['file']].append({ 
            'start': resource['uri']['start'],
            'end': resource['uri']['end'],
            'requirementUid': resource['requirementUid']
        })

    return files_to_resources


def requirements_by_file(json_resources):
    files_to_requirements = {}
    for resource in json_resources:
        # if a list for this file already exists,
        # just append a new entry to the list
        if resource['uri']['file'] not in files_to_requirements:
            files_to_requirements[resource['uri']['file']] = set()

        files_to_requirements[resource['uri']['file']].add(resource['requirementUid'])

    return files_to_requirements


def dict_to_region(view, json_dict):
    start_row = json_dict['start']['row']
    start_col = json_dict['start']['col']
    end_row = json_dict['end']['row']
    end_col = json_dict['end']['col']

    start_point = view.text_point(start_row, start_col)
    end_point = view.text_point(end_row, end_col)

    return sublime.Region(start_point, end_point)


def file_name_from_view(view, main_folder):
    file_name = view.file_name()
    if file_name is None:
        return file_name
    else:
        # grab the name of the current file excluding the folder name before it
        return file_name[len(main_folder + '/'):]


def resource_map_to_json(resource_map):
    json_resources = []

    for file_name, resources in resource_map.iteritems():
        for resource in resources:
            json_resources.append({
                'requirementUid': resource['requirementUid'],
                'uri': {
                    'file': file_name,
                    'start': resource['start'],
                    'end': resource['end']
                }
            })

    return json_resources


def requirements_at_selection(view, requirement_uids, map_of_all_requirements):
    # Find all regions with requirements in the current view
    requirement_regions = {}
    for uid in requirement_uids:
        requirement_regions[uid] = view.get_regions(c_rsrc + str(uid))

    uids_of_requirements_at_selection = []

    # Filter the regions by only the ones that are partially or wholly 
    # encapsulated in the currently selected text
    for uid, regions in requirement_regions.iteritems():
        is_requirement_in_selection = False
        for requirement_region in regions:
            for selected_region in view.sel():
                if requirement_region.intersects(selected_region):
                    is_requirement_in_selection = True
                    break

            if is_requirement_in_selection:
                break

        if is_requirement_in_selection:
            uids_of_requirements_at_selection.append(uid)

    # We have uids of all requirements, now get the requirements themselves into a list
    retval = []
    for uid in uids_of_requirements_at_selection:
        if uid not in map_of_all_requirements:
            retval.append(deprecated_resource(uid))
        else:
            retval.append(map_of_all_requirements[uid])

    return retval


def requirement_string(requirement):
    return [
        "requirement " + str(requirement['uid']) + ": " + requirement['name'],
        requirement['description']
    ]


def resources_at_cursor(view, cursor_pos, requirement_uids):
    retval = []

    for uid in requirement_uids:
        key = c_rsrc + str(uid)
        regions = view.get_regions(key)
        for index in range(len(regions)):
            region = regions[index]
            if region.contains(cursor_pos):
                retval.append((region, uid, index))

    return retval    


def smallest_resource_at_cursor(view, cursor_pos, requirement_uids):
    resources = resources_at_cursor(view, cursor_pos, requirement_uids)
    if len(resources) == 0:
        return None, None, None

    smallest_resource_index = 0
    for index in range(1, len(resources)):
        current_smallest, _, _ = resources[smallest_resource_index]
        resource, _, _ = resources[index]
        if resource.size() < current_smallest.size():
            smallest_resource_index = index

    return resources[smallest_resource_index]


def add_regions_flags(is_showing_resources):
    if is_showing_resources:
        return sublime.PERSISTENT | sublime.DRAW_EMPTY | sublime.DRAW_OUTLINED
    else:
        return sublime.PERSISTENT | sublime.HIDDEN


"""
NOTE: Technically this is not a pure function (in fact it just does side effects)
      but it is used as a local mutation inside otherwise pure functions for
      optimization purposes.
"""
def write_spec_output_requirement(c_string_io, requirement):
    c_string_io.write("\n")
    c_string_io.write("------------\n")
    c_string_io.write("requirement #" + str(requirement['uid']) + "\n")
    c_string_io.write("Name: " + requirement['name'] + "\n")
    c_string_io.write("Description:\n")
    c_string_io.write(requirement['description'] + "\n")


"""
NOTE: Technically this is not a pure function (in fact it just does side effects)
      but it is used as a local mutation inside otherwise pure functions for
      optimization purposes.
"""
def write_spec_output_resource(c_string_io, resource):
    c_string_io.write(
        resource['file'] + ":" + 
        str(resource['start']['row'] + 1) + ":" + 
        str(resource['start']['col']) + "-" +
        str(resource['end']['row'] + 1) + ":" + 
        str(resource['end']['col']) + "\n")


def spec_scope_output(spec_scope):
    from cStringIO import StringIO
    output = StringIO()

    output.write("Unaddressed requirements\n")
    output.write("====================\n")
    for requirement in spec_scope['requirementsNotAddressed']:
        write_spec_output_requirement(output, requirement)

    output.write("\n\n")
    output.write("Unassociated (potentially deprecated) resources\n")
    output.write("===============================================\n")
    for resource in spec_scope['resourcesNotLinked']:
        write_spec_output_resource(output, resource['uri'])

    return output.getvalue()


def diff_scope_output(diff_scope):
    from cStringIO import StringIO
    output = StringIO()

    output.write("New or Unaddressed requirements\n")
    output.write("===========================\n")
    for requirement in diff_scope['requirementsToAddress']:
        write_spec_output_requirement(output, requirement)

    output.write("\n\n")
    output.write("Resources to Update\n")
    output.write("===================\n")
    for resource in diff_scope['resourcesToUpdate']:
        write_spec_output_resource(output, resource['uri'])

    output.write("\n\n")
    output.write("Deprecated Resources\n")
    output.write("====================\n")
    for resource in diff_scope['deprecatedResources']:
        write_spec_output_resource(output, resource['uri'])

    return output.getvalue()


def resources_by_requirement(requirement, _resources_by_file):
    from cStringIO import StringIO
    output = StringIO()

    write_spec_output_requirement(output, requirement)

    output.write("\n")
    output.write("Associated Resources:\n")

    for file_name, resources in _resources_by_file.iteritems():
        for resource in resources:
            resource['file'] = file_name
            if requirement['uid'] == resource['requirementUid']:
                write_spec_output_resource(output, resource)
    return output.getvalue()


# ==============================================================================
#  Procedures
# ==============================================================================

def erase_region_at_index(view, key, index):
    # I can't erase particular regions of a view.
    # I can only erase all regions of a view given a key.
    # So, this deletes all regions of the resource's key
    # and then add the regions that weren't the resource back
    # to the view. 
    regions = view.get_regions(key)
    del regions[index]
    view.erase_regions(key)
    view.add_regions(key, regions, c_scope, c_icon, 
        add_regions_flags(g_is_showing_resources))


def display_in_new_file(window, to_display):
    new_view = window.new_file()
    edit = new_view.begin_edit()
    new_view.insert(edit, 0, to_display)
    new_view.end_edit(edit)


def insert_file_slice(view, file_hook, insert_pos):
    hook_to_check = g_main_folder + "/" + file_hook
    file_slice = fileslices.slice_from_hook(hook_to_check, 3)
    if not file_slice.is_ok():
        print file_slice.err
        return

    to_insert = "\n" + fileslices.slice_to_string(file_slice.ok) + "\n"
    edit = view.begin_edit()
    num_chars_inserted = view.insert(edit, insert_pos, to_insert)
    view.end_edit(edit)

    region = sublime.Region(insert_pos, insert_pos + num_chars_inserted)
    view.add_regions(file_hook, [region], c_scope, c_icon,
        sublime.HIDDEN | sublime.HIDE_ON_MINIMAP)


# ==============================================================================
#  Sublime Event Listeners
# ==============================================================================

class MarkResourcesOnLoad(sublime_plugin.EventListener):
    def on_load(self, view):
        if not g_main_has_run:
            main()

        file_name = file_name_from_view(view, g_main_folder)
        if file_name is None:
            print "Current file does not have a name."
            return

        try:
            # get all text regions that are considered "resources"
            # in the current file
            resources = g_resources_by_file[file_name]

        except KeyError:
            resources = []
            return
        
        # mark all the text regions as resources
        regions_by_uid = {}
        for resource in resources:
            if resource['requirementUid'] not in regions_by_uid:
                regions_by_uid[resource['requirementUid']] = []

            regions_by_uid[resource['requirementUid']].append(dict_to_region(view, resource))

        # create the marked regions, but hidden
        for uid, region in regions_by_uid.iteritems():
            key = c_rsrc + str(uid)
            view.add_regions(key, region, c_scope, c_icon,
                sublime.PERSISTENT | sublime.HIDDEN)


class WriteResourcesOnSave(sublime_plugin.EventListener):
    def on_post_save(self, view):
        global g_resources_by_file

        file_name = file_name_from_view(view, g_main_folder)
        if file_name is None:
            print "Current file does not have a name."
            print "Note: this should never happen because this event triggers after a file is saved."
            return

        if file_name == "resources.json":
            return

        # reset all resources in the current file
        try:
            requirement_uids = g_requirements_by_file[file_name]
        except KeyError:
            return

        g_resources_by_file[file_name] = []

        for uid in requirement_uids:
            regions = view.get_regions(c_rsrc + str(uid))

            for region in regions:
                start_row, start_col = view.rowcol(region.begin())
                end_row, end_col = view.rowcol(region.end())
                g_resources_by_file[file_name].append({
                    'requirementUid': uid,
                    'start': {
                        'row': start_row,
                        'col': start_col
                    },
                    'end': {
                        'row': end_row,
                        'col': end_col
                    }
                })

        # make a list for resources.json and save it out
        json_resources = resource_map_to_json(g_resources_by_file)

        resources_file = open(g_main_folder + '/resources.json', 'w')
        json.dump(json_resources, resources_file, indent=4)


# ==============================================================================
#  Sublime Commands
# ==============================================================================

"""
Highlights all regions of text that have been marked as associated with a
requirement.
"""
class ShowResourcesCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        global g_is_showing_resources

        # Get the current file
        file_name = file_name_from_view(self.view, g_main_folder)
        if file_name is None:
            print "Current file does not have a name."
            return

        requirement_uids = g_requirements_by_file[file_name]

        # For every requirement, highlight the text associated with that requirement
        for uid in requirement_uids:
            key = c_rsrc + str(uid)
            regions = self.view.get_regions(key)
            self.view.erase_regions(key)

            # show the marked regions
            self.view.add_regions(key, regions, c_scope, c_icon,
                sublime.PERSISTENT | sublime.DRAW_EMPTY | sublime.DRAW_OUTLINED)

        g_is_showing_resources = True


"""
Removes the highlights added by ShowResourcesCommand
"""
class HideResourcesCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        global g_is_showing_resources

        # Get the current file
        file_name = file_name_from_view(self.view, g_main_folder)        
        if file_name is None:
            print "Current file does not have a name."
            return

        requirement_uids = g_requirements_by_file[file_name]    
        regions = []

        # For every requirement, find its associated text and de-highlight it
        for uid in requirement_uids:
            key = c_rsrc + str(uid)
            regions = self.view.get_regions(key)
            self.view.erase_regions(key)

            # hide the marked regions
            self.view.add_regions(key, regions, c_scope, c_icon,
                sublime.PERSISTENT | sublime.HIDDEN)

        g_is_showing_resources = False

"""
Opens a new window that displays all requirements associated with the current
selection or cursor position.
"""
class RequirementsAtSelectionCommand(sublime_plugin.WindowCommand):
    def run(self):
        # Get the current file
        active_view = self.window.active_view()
        if active_view is None:
            print "There is no active view."
            return

        file_name = file_name_from_view(active_view, g_main_folder)
        if file_name is None:
            print "Current file does not have a name."
            return

        requirements = requirements_at_selection(
            active_view, 
            g_requirements_by_file[file_name], 
            g_requirements_by_uid)

        string_to_display = ""
        for requirement in requirements:
            string_to_display += "\n"
            string_to_display += "------------\n"
            string_to_display += "requirement #" + str(requirement['uid']) + "\n"
            string_to_display += "Name:" + requirement['name'] + "\n"
            string_to_display += "Description:\n"
            string_to_display += requirement['description'] + "\n"

        display_in_new_file(self.window, string_to_display)


"""
Adds a new resource or reassigns a resource to a different requirement.

Pops up a dialog box to select a requirement.

For each cursor:
- If a region of text is selected, then this command will add the selected 
region as a resource.

- If no region of text is selected, then this command will try to assign the
smallest resource containing the cursor to the selected requirement.

- If no region of text is selected and the cursor is not currently contained in
any resource, this command will do nothing.
"""
class AssignResourceCommand(sublime_plugin.WindowCommand):
    def run(self):
        global g_requirements_by_file

        # Get the current file
        active_view = self.window.active_view()
        if active_view is None:
            print "There is no active view."
            return

        file_name = file_name_from_view(active_view, g_main_folder)
        if file_name is None:
            print "Current file does not have a name."
            return

        requirement_strings = []
        string_uids = []
        for uid, requirement in g_requirements_by_uid.iteritems():
            requirement_strings.append(requirement_string(requirement))
            string_uids.append(uid)

        def on_requirement_select(index):
            # "index" will be -1 if no requirement was selected
            if index == -1:
                return

            requirement_uid = string_uids[index]
            key_to_add = c_rsrc + str(requirement_uid)
            regions_to_add = active_view.get_regions(key_to_add)

            for region in active_view.sel():
                # if the selection covers a region of text
                if region.size() > 0:
                    if file_name not in g_requirements_by_file:
                        g_requirements_by_file[file_name] = set()
                    g_requirements_by_file[file_name].add(requirement_uid)

                    regions_to_add.append(region)

                    active_view.add_regions(key_to_add, regions_to_add, 
                        c_scope, c_icon, add_regions_flags(g_is_showing_resources))

                # otherwise the region is a cursor at a location
                else:
                    resource, uid, index = smallest_resource_at_cursor(
                        active_view, region.begin(), g_requirements_by_file[file_name])
                    key = c_rsrc + str(uid)

                    if resource is None:
                        return

                    erase_region_at_index(active_view, key, index)
                    regions_to_add.append(resource)
                    active_view.add_regions(key_to_add, regions_to_add, 
                        c_scope, c_icon, add_regions_flags(g_is_showing_resources))

        self.window.show_quick_panel(requirement_strings, on_requirement_select)


class DissociateResourceCommand(sublime_plugin.WindowCommand):
    def run(self):
        # Get the current file
        active_view = self.window.active_view()
        if active_view is None:
            print "There is no active view."
            return

        file_name = file_name_from_view(active_view, g_main_folder)
        if file_name is None:
            print "Current file does not have a name."
            return

        for cursor_pos in active_view.sel():
            resources_to_delete = resources_at_cursor(
                active_view, cursor_pos.begin(), g_requirements_by_file[file_name])

            if len(resources_to_delete) == 0:
                print "No resources to dissociate at cursor position."
                return

            requirement_strings = []
            for _, uid, _ in resources_to_delete: 
                if uid not in g_requirements_by_uid:
                    _requirement_string = requirement_string(deprecated_resource(uid))
                else:
                    _requirement_string = requirement_string(g_requirements_by_uid[uid])

                requirement_strings.append(_requirement_string)

            def on_requirement_select(index):
                # "index" will be -1 if no requirement was selected
                if index == -1:
                    return

                resource, uid, regions_index = resources_to_delete[index]
                key = c_rsrc + str(uid)
                erase_region_at_index(active_view, key, regions_index)

            self.window.show_quick_panel(requirement_strings, on_requirement_select)


class SpecScopeCommand(sublime_plugin.WindowCommand):
    def run(self):
        try:
            cmd = g_spec_path + " scope --spec \"" + g_main_folder + "/spec.json\" --resources \"" + g_main_folder + "/resources.json\""
            proc = subprocess.Popen([cmd],
                stdout=subprocess.PIPE, shell=True)
            spec_output = proc.communicate()[0]
        except OSError:
            print "The 'spec' command exited with a non-zero return code!"
            print "Check your 'spec_path' setting to make sure it's pointing to the right executable!"
            print "Your spec path is: " + g_spec_path
            return

        spec_scope = json.loads(spec_output)
        details = spec_scope_output(spec_scope)

        display_in_new_file(self.window, details) 


class DiffScopeCommand(sublime_plugin.WindowCommand):
    def run(self):
        def on_diff_path_entered(diff_path):
            # first make sure that the diff_path is a valid file before blindly
            # running a shell command with it
            try:
                test1 = open(diff_path)
            except IOError:
                print "The diff file given: " + diff_path + " does not exist!"
                return

            try:
                test2 = open(g_spec_path)
            except IOError:
                print "The spec executable at: " + g_spec_path + " does not exist!"
                return

            try:
                cmd = g_spec_path + " diff-scope --diff \"" + diff_path + "\" --resources \"" + g_main_folder + "/resources.json\""
                proc = subprocess.Popen([cmd],
                    stdout=subprocess.PIPE, shell=True)
                spec_output = proc.communicate()[0]
            except OSError as err:
                print "The 'spec' command exited with a non-zero return code!"
                print "Check your 'spec_path' setting to make sure it's pointing to the right executable!"
                print "Your spec path is: " + g_spec_path
                return

            diff_scope = json.loads(spec_output)
            details = diff_scope_output(diff_scope)
            display_in_new_file(self.window, details)

        self.window.show_input_panel(
            "Path to diff file", "./diff.json", on_diff_path_entered, None, None)


class ReloadSpecCommand(sublime_plugin.TextCommand):
    def run(self, edit):
        try:
            spec_file = open(g_main_folder + '/spec.json', 'r')
        except IOError:
            print "Could not find spec.json at the root of the project."
            return

        with spec_file:
            json_spec = json.load(spec_file)
            g_requirements_by_uid = requirements_by_uid(json_spec)


class ResourcesForRequirement(sublime_plugin.WindowCommand):
    def run(self):
        requirement_strings = []
        string_uids = []
        for uid, requirement in g_requirements_by_uid.iteritems():
            requirement_strings.append(requirement_string(requirement))
            string_uids.append(uid)

        def on_requirement_select(index):
            # "index" will be -1 if no requirement was selected
            if index == -1:
                return

            resource_listing = resources_by_requirement(
                g_requirements_by_uid[string_uids[index]], g_resources_by_file)
            display_in_new_file(self.window, resource_listing)

        self.window.show_quick_panel(requirement_strings, on_requirement_select)


class OpenFileOnLine(sublime_plugin.TextCommand):
    def run(self, edit):
        for selected_region in self.view.sel():
            regions = self.view.lines(self.view.line(selected_region))
            for region in regions:
                hook = self.view.substr(region)
                hook = hook[:hook.find('-')]
                self.view.window().open_file(hook, sublime.ENCODED_POSITION | sublime.TRANSIENT) 


class PeekFileOnLine(sublime_plugin.TextCommand):
    def run(self, edit):
        for selected_region in self.view.sel():
            regions = self.view.lines(self.view.line(selected_region))
            for region in regions:
                hook = self.view.substr(region)
                if len(self.view.get_regions(hook)) > 0:
                    self.view.run_command("hide_file_on_line")
                    return

                insert_file_slice(self.view, hook, region.end() + 1)


class HideFileOnLine(sublime_plugin.TextCommand):
    def run(self, edit):
        for selected_region in self.view.sel():
            hook_regions = self.view.lines(self.view.line(selected_region))
            for hook_region in hook_regions:
                hook = self.view.substr(hook_region)
                file_content_regions = self.view.get_regions(hook)

                for region in file_content_regions:
                    edit = self.view.begin_edit()
                    self.view.erase(edit, region)
                    self.view.end_edit(edit)

                self.view.erase_regions(hook)


# ==============================================================================
#  Main
# ==============================================================================

c_rsrc = "rsrc"
c_scope = "meta.block"
c_icon = "bookmark"
c_base_name = "Spec.sublime-settings"
g_main_folder = ""
g_requirements_by_uid = {}
g_resources_by_file = {}
g_requirements_by_file = {}
g_main_has_run = False
g_is_showing_resources = False
g_spec_path = "spec"

def main():
    global g_main_folder
    global g_requirements_by_uid
    global g_resources_by_file
    global g_requirements_by_file
    global g_main_has_run
    global g_spec_path

    if g_main_has_run:
        return

    if sublime.active_window() is None or len(sublime.active_window().folders()) == 0:
        return

    g_main_folder = sublime.active_window().folders()[0]

    if g_main_folder is None:
        print "No main folder!"
        return

    print "Main folder: " + g_main_folder

    try:
        spec_file = open(g_main_folder + '/spec.json', 'r')
    except IOError:
        print "Could not find spec.json at the root of the project."
        return

    try:
        resources_file = open(g_main_folder + '/resources.json', 'r')
    except IOError:
        print "Could not find resources.json at the root fo the project."
        return

    settings = sublime.load_settings(c_base_name)
    g_spec_path = settings.get("spec_path", "spec")

    def set_spec_path(spec_path):
        global g_spec_path
        g_spec_path = spec_path

    settings.add_on_change("spec_path", set_spec_path)

    with spec_file and resources_file:
        json_spec = json.load(spec_file)
        json_resources = json.load(resources_file)
        g_requirements_by_uid = requirements_by_uid(json_spec)
        g_resources_by_file = resources_by_file(json_resources)
        g_requirements_by_file = requirements_by_file(json_resources)
        g_main_has_run = True


main()
