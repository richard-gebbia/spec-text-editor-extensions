import itertools

# Like Result from Rust or Elm; should probably break this out into its own module
class Result:
    def __init__(self, err=None, ok=None):
        if err is not None and ok is not None:
            # please stop using this data structure incorrectly
            err = None

        self.err = err
        self.ok = ok

    def is_ok(self):
        return self.ok is not None and self.err is None

    def map(self, fn):
        if self.is_ok():
            return Result(ok=fn(self.ok))

    def with_default(self, default):
        if self.is_ok():
            return self.ok
        else:
            return default


def ok(val):
    return Result(ok=val)

def err(val):
    return Result(err=val)


class SlicePlusExtra:
    def __init__(self, region, line_start, slice_start_row, slice_start_col, slice_end_row, slice_end_col):
        self.region = region
        self.line_start = line_start
        self.slice_start_row = slice_start_row
        self.slice_start_col = slice_start_col
        self.slice_end_row = slice_end_row
        self.slice_end_col = slice_end_col


# Note: Due to the way iterators work, this function cannot tell you whether the
#       start point of the slice is greater than the iterator's length. In this 
#       event, this function will return an iterator that immediately 
#       terminates.
def make_slice(iter_to_slice, start_row, start_col, end_row, end_col, num_extra_lines):
    region_start = max(0, start_row - num_extra_lines)
    region_end = end_row + num_extra_lines + 1

    if region_end < 0:
        return err("The slice's end point was negative: " + str(region_end))
    elif region_end < region_start:
        return err("The slice's end point, " + str(region_end) +", is before the slice's start point, " + str(region_start))

    region = itertools.islice(iter_to_slice, region_start, region_end)
    return ok(
        SlicePlusExtra(
            region, 
            region_start, 
            start_row - region_start, 
            start_col, 
            end_row - region_start, 
            end_col))


# Note: Due to the way iterators work, this function cannot tell you whether the
#       start point of the slice is greater than the file's length. In this 
#       event, this function will return an iterator that immediately 
#       terminates.
def file_slice(file_name, start_row, start_col, end_row, end_col, num_extra_lines):
    try:
        file_iter = open(file_name, 'r')
    except IOError:
        return err("Could not open file: " + file_name)

    return make_slice(file_iter, start_row, start_col, end_row, end_col, num_extra_lines)


def slice_from_hook(hook, num_extra_lines):
    split_hook = hook.split('-')
    if len(split_hook) != 1 and len(split_hook) != 2:
        return err("The file hook needs to be in the format <filename>:<startline>:<startcol>-<endline>:<endcol>")

    file_name_and_start = split_hook[0]

    split_file_name_and_start = file_name_and_start.split(':')
    if len(split_file_name_and_start) != 2 and len(split_file_name_and_start) != 3:
        return err("The file hook needs to be in the format <filename>:<startline>:<startcol>-<endline>:<endcol>")

    file_name = split_file_name_and_start[0]
    try:
        start_row = int(split_file_name_and_start[1]) - 1
    except ValueError:
        return err("The start line: " + split_file_name_and_start[1] + ", must be a number!")

    if len(split_file_name_and_start) == 3:
        try:
            start_col = int(split_file_name_and_start[2])
        except ValueError:
            return err("The start column: " + split_file_name_and_start[2] + ", must be a number!")
    else:
        start_col = 0

    if len(split_hook) == 2:
        end_row_and_col = split_hook[1]

        split_end_row_and_col = end_row_and_col.split(':')
        if len(split_end_row_and_col) != 1 and len(split_end_row_and_col) != 2:
            return err("The file hook needs to be in the format <filename>:<startline>:<startcol>-<endline>:<endcol>")

        try:
            end_row = int(split_end_row_and_col[0]) - 1
        except ValueError:
            return err("The end line: " + split_end_row_and_col[0] + ", must be a number!")

        if len(split_end_row_and_col) == 2:
            try:
                end_col = int(split_end_row_and_col[1])
            except ValueError:
                return err("The end column: " + split_end_row_and_col[1] + ", must be a number!")
        else:
            end_col = 0

    return file_slice(file_name, start_row, start_col, end_row, end_col, num_extra_lines)


def slice_to_strings(slice_plus_extra):
    retval = []
    i = 0

    for line in slice_plus_extra.region:
        file_row = slice_plus_extra.line_start + i
        if i >= slice_plus_extra.slice_start_row and i <= slice_plus_extra.slice_end_row:
            retval.append(("{0:5}".format(file_row + 1) + ": " + line).rstrip())
        else:
            retval.append(("{0:5}".format(file_row + 1) + "  " + line).rstrip())
        i += 1

    return retval


def slice_to_string(slice_plus_extra):
    from cStringIO import StringIO
    output = StringIO()

    for line in slice_to_strings(slice_plus_extra):
        output.write(line + "\n")

    return output.getvalue()

