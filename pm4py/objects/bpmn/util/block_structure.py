from pm4py.objects.bpmn.obj import BPMN
from pm4py.objects.petri_net.utils.petri_utils import add_arc_from_to, get_place_by_name, remove_arc, remove_place, get_transition_by_name, \
    add_reset_arc_from_to, get_place_by_prefix_postfix, is_reset_arc, is_normal_arc
from pm4py.objects.bpmn.util.bpmn_utils import get_boundary_events_of_activity, get_all_nodes_inside_process, get_subprocesses_sorted_by_depth, \
    get_termination_events_of_subprocess, get_node_by_id

""" global variable for the collection of gateways to identify blocks """
stack = []

def flow_paths_from_to(a, b, curr_path=[], forbidden=[]):
    if a == b:
        return [[b]]
    elif a in curr_path or isinstance(a.get_target(), BPMN.EndEvent) or a.get_target() in forbidden:
        return [[]]
    return [[a] + path for flow in a.get_target().get_out_arcs() for path in flow_paths_from_to(flow, b, curr_path + [a], forbidden) if \
         len(path) > 0 and b in path]


def petri_net_paths_from_to(a, b, curr_path=[], include_impasse=False):
    """
    Returns all places and transitions on a path between a and b
    """
    if a == b:
        return [[b]]
    elif a in curr_path:
        return [[]]
    elif len(a.out_arcs) == 0:
        if include_impasse:
            return [[a]]
        else:
            return [[]]
    return [[a] + path for arc in a.out_arcs for path in petri_net_paths_from_to(arc.target, b, curr_path + [a], include_impasse=include_impasse) if \
         is_normal_arc(arc) and b in path]

def paths_from_to(a, b, curr_path=[]):
    """
    Returns all BPMN objects on a path between a and b
    """
    if a == b:
        return [[b]]
    elif a in curr_path:
        return [[]]
    elif isinstance(a, BPMN.EndEvent):
        return [[]]
    return [[a] + path for flow in a.get_out_arcs() for path in paths_from_to(flow.get_target(), b, curr_path + [a]) if \
         len(path) > 0 and b in path]


def paths_from_to_advanced(a, b, graph, curr_path=[]):
    """
    Returns all BPMN objects on a path between a and b
    """
    if a == b:
        return [[b]]
    elif a in curr_path:
        return [[]]
    elif isinstance(a, BPMN.EndEvent):
        return [[]]
    elif isinstance(a, BPMN.SubProcess):
        return [[a] + path for flow in a.get_out_arcs() for path in paths_from_to_advanced(flow.get_target(), b, graph, curr_path + [a]) if \
         len(path) > 0 and b in path] + \
           [[a] + path for boundary_event in get_boundary_events_of_activity(a.get_id(), graph) \
               for path in paths_from_to_advanced(boundary_event, b, graph, curr_path + [a]) if len(path) > 0 and b in path]
    else:
        return [[a] + path for flow in a.get_out_arcs() for path in paths_from_to_advanced(flow.get_target(), b, graph, curr_path + [a]) if \
         len(path) > 0 and b in path] 

def paths_from(a, curr_path=[]):
    """
    all paths from a node "a" to an end event or impasse without passing anything twice
    """
    if isinstance(a, BPMN.EndEvent):
        return [[a]]
    if a in curr_path:
        return [[]]
    return [[a] + path for flow in a.get_out_arcs() for path in paths_from(flow.get_target(), curr_path + [a])]

def paths_to(a, curr_path=[]):
    """
    all paths from a start event/boundary event or impasse to "a"
    """
    if isinstance(a, (BPMN.StartEvent, BPMN.BoundaryEvent)):
        return [[a]]
    if a in curr_path:
        return [[]]
    return [path + [a] for flow in a.get_in_arcs() for path in paths_to(flow.get_source(), [a] + curr_path)]

    
def paths_to_end(a, curr_path=[]):
    """
    all paths from a node a to an end event without passing a twice
    """
    if isinstance(a, BPMN.EndEvent):
        return [[a]]
    if a in curr_path:
        return [[]]
    return [[a] + path for flow in a.get_out_arcs() for path in paths_to_end(flow.get_target(), curr_path + [a]) if len(path) > 0 and \
        isinstance(path[-1], BPMN.EndEvent)]

def paths_to_start(a, curr_path=[]):
    """
    all paths from a node a to a start event/boundary event without passing a twice
    """
    if isinstance(a, (BPMN.StartEvent, BPMN.BoundaryEvent)):
        return [[a]]
    if a in curr_path:
        return [[]]
    return [path + [a] for flow in a.get_in_arcs() for path in paths_to_start(flow.get_source(), [a] + curr_path) if len(path) > 0 and \
        isinstance(path[0], (BPMN.StartEvent, BPMN.BoundaryEvent))]

def build_block(a,b):
    trimmed_start_paths = paths_to(b)
    for i, path in enumerate(trimmed_start_paths):
        try:
            a_ind = path.index(a)
            b_ind = path.index(b)
        except:
            return False
        trimmed_start_paths[i] = path[a_ind:b_ind+1]

    start_paths = []
    for path in trimmed_start_paths:
        if path not in start_paths:
            start_paths.append(path)

    trimmed_end_paths = paths_from(a)
    for i, path in enumerate(trimmed_end_paths):
        try:
            a_ind = path.index(a)
            b_ind = path.index(b)
        except:
            return False
        trimmed_end_paths[i] = path[a_ind:b_ind+1]

    end_paths = []
    for path in trimmed_end_paths:
        if path not in end_paths:
            end_paths.append(path)

    for path in start_paths:
        if path not in end_paths:
            return False
    # return true if the paths have the same length, however, there must be at least
    # two paths to the start and to the end, so it is assumed that two gateways do not
    # form a block when they have two flow connections without any activity in between
    return len(start_paths) == len(end_paths) and len(start_paths) > 1

def _encapsulating_blocks(node, blocks, graph):
    global stack
    # fill or remove stack if we see a parallel gateway
    if isinstance(node, (BPMN.ParallelGateway, BPMN.InclusiveGateway)):
        if node.get_gateway_direction() == BPMN.Gateway.Direction.DIVERGING or len(node.get_out_arcs()) > 1:
            # corresponding converging gateway in stack
            if len(stack) > 0 and len(stack[-1].get_in_arcs()) > 1:
                del stack[-1]
            else:
                stack.append(node)
        else: # converging gateway, always add to stack
            if len(stack) > 0:
                del stack[-1]
    # multiple input flows
    if len(node.get_in_arcs()) > 1:
        if isinstance(node, (BPMN.ParallelGateway, BPMN.InclusiveGateway)):
            predecessor = node.get_in_arcs()[0].get_source()
            return _encapsulating_blocks(predecessor, blocks, graph)
        # XOR- join
        else:
            # ignore normal XOR join
            if node not in blocks:
                predecessor = node.get_in_arcs()[0].get_source()
                return _encapsulating_blocks(predecessor, blocks, graph)
            # loop detected
            else:
                # the flow that is not the RE-DO Child of the loop always has a shorter path to the start event
                # than the Re-DO Child
                flow_paths = dict()
                for flow in node.get_in_arcs():
                    flow_paths[flow] = min(map(lambda x: len(x), paths_to_start(flow.get_source())))
                path_lengths = sorted(flow_paths.items(), key=lambda item: item[1])
                predecessor = path_lengths[0][0].get_source()
                return _encapsulating_blocks(predecessor, blocks, graph)
    # move on with proceeding node
    elif len(node.get_in_arcs()) == 1:
        predecessor = node.get_in_arcs()[0].get_source()
        return _encapsulating_blocks(predecessor, blocks, graph)
    # move on with subprocess
    elif isinstance(node, BPMN.BoundaryEvent):
        subprocess = get_node_by_id(node.get_activity(), graph)
        return _encapsulating_blocks(subprocess, blocks, graph)
    # start gateway reached
    else:
        return stack

def encapsulating_blocks(node, blocks, graph):
    global stack
    stack = []
    return _encapsulating_blocks(node, blocks, graph)

def _block(node, bpmn_graph):
    global stack
    if isinstance(node, BPMN.Gateway):
        if isinstance(node, (BPMN.ParallelGateway, BPMN.InclusiveGateway)):
            if node.get_gateway_direction() == BPMN.Gateway.Direction.DIVERGING or len(node.get_out_arcs()) > 1:
                # add gateway to stack
                stack.append(node)
                blocks = {}
                for outflow in node.get_out_arcs():
                    b = _block(outflow.get_target(), bpmn_graph)
                    blocks = {**blocks, **b}
                join = blocks[node]
                if len(stack) > 0:
                    del stack[-1]
                next_node = join.get_out_arcs()[0].get_target()
                further_blocks = _block(next_node, bpmn_graph)
                res = {**blocks, **further_blocks}
                return res
            elif node.get_gateway_direction() == BPMN.Gateway.Direction.CONVERGING or len(node.get_in_arcs()) > 1:
                b = {}
                if len(stack) > 0:
                    if type(node) != type(stack[-1]):
                        # remove XOR split gateway from stack since it does not have a partner
                        del stack[-1]
                    b[stack[-1]] = node
                return b
        
        elif isinstance(node, (BPMN.ExclusiveGateway)):
            if node.get_gateway_direction() == BPMN.Gateway.Direction.DIVERGING or len(node.get_out_arcs()) > 1:
                if len(stack) > 0 and isinstance(stack[-1], BPMN.ExclusiveGateway) and \
                    len(stack[-1].get_in_arcs()) > 1 and \
                    len([n for n in bpmn_graph.get_nodes() if n.get_process() == node.get_process() and \
                    isinstance(n, BPMN.ExclusiveGateway) and build_block(node, n) ]) == 0:
                    # loop exit detected
                    b = {}
                    b[stack[-1]] = node
                    return b
                else:
                    # add gateway to stack
                    stack.append(node)
                    blocks = {}
                    for outflow in node.get_out_arcs():
                        b = _block(outflow.get_target(), bpmn_graph)
                        blocks = {**blocks, **b}
                    if node in blocks:
                        join = blocks[node]
                        if len(stack) > 0:
                            del stack[-1]
                        next_node = join.get_out_arcs()[0].get_target()
                        further_blocks = _block(next_node, bpmn_graph)
                        return {**blocks, **further_blocks}
                    else:
                        return blocks
            elif node.get_gateway_direction() == BPMN.Gateway.Direction.CONVERGING or len(node.get_in_arcs()) > 1:
                b = {}
                if len(stack) > 0 and type(node) == type(stack[-1]) and build_block(stack[-1], node):
                    b[stack[-1]] = node
                    return b
                else: 
                    # they do not form a block, i.e., the current join gate refers to a loop entrance or
                    # subprocess-boundary branch connector
                    if len([flow for flow in node.get_in_arcs() if isinstance(flow.get_source(), BPMN.SubProcess)]) > 0 and \
                        len([flow for flow in node.get_in_arcs() if len(list(filter(lambda x: not isinstance(x[0], BPMN.BoundaryEvent), 
                        paths_to_start(flow.get_source())))) == 0]) == len(node.get_in_arcs()) - 1: 
                            # XOR join detected that brings exception flow of subprocess back on the track
                            return _block(node.get_out_arcs()[0].get_target(), bpmn_graph)

                    # loop entrance detected
                    stack.append(node)
                    next_node = node.get_out_arcs()[0].get_target()
                    b = _block(next_node, bpmn_graph)

                    loop_exit = b[node]
                    if len(stack) > 0:
                        del stack[-1]
                    
                    flow_paths = dict()
                    for flow in loop_exit.get_out_arcs():
                        flow_paths[flow] = min(map(lambda x: len(x), paths_to_end(flow.get_target())))
                    path_lengths = sorted(flow_paths.items(), key=lambda item: item[1])
                    loop_successor = path_lengths[0][0].get_target()

                    further_blocks = _block(loop_successor, bpmn_graph)
                    return {**b, **further_blocks}
    elif isinstance(node, BPMN.EndEvent):
        if len(stack) > 0:
            del stack[-1]
        return {}
    else:
        return _block(node.get_out_arcs()[0].get_target(), bpmn_graph)

def block(node, bpmn_graph):
    global stack
    stack = []
    return _block(node, bpmn_graph)
    