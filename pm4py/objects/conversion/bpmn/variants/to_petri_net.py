'''
    This file is part of PM4Py (More Info: https://pm4py.fit.fraunhofer.de).

    PM4Py is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    PM4Py is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with PM4Py.  If not, see <https://www.gnu.org/licenses/>.
'''
import uuid
from enum import Enum

from pm4py.objects.petri_net.utils import reduction
from pm4py.objects.petri_net.obj import PetriNet, Marking
from pm4py.objects.petri_net.utils.petri_utils import add_arc_from_to, get_place_by_name, remove_arc, remove_place, get_transition_by_name, remove_transition, get_place_by_prefix_postfix
from pm4py.objects.bpmn.util.bpmn_utils import get_boundary_events_of_activity, get_all_nodes_inside_process, get_subprocesses_sorted_by_depth, \
get_termination_events_of_subprocess, get_termination_events_of_subprocess_for_pnet, get_all_direct_child_subprocesses, get_all_child_subprocesses
from pm4py.util import exec_utils
from collections import defaultdict
from itertools import chain, combinations


# specifies whether or not boundary transitions and events should be treated as tasks, i.e no silent transitions
INCLUDE_EVENTS = "include_events"

class Parameters(Enum):
    USE_ID = "use_id"


def power_set(iterable, min=1):
    s = list(iterable)
    return chain.from_iterable(combinations(s, r) for r in range(min, len(s)+1))

def build_digraph_from_petri_net(net):
    """
    Builds a directed graph from a Petri net
        (for the purpose to add invisibles between inclusive gateways)

    Parameters
    -------------
    net
        Petri net

    Returns
    -------------
    digraph
        Digraph
    """
    import networkx as nx
    graph = nx.DiGraph()
    for place in net.places:
        graph.add_node(place.name)
    for trans in net.transitions:
        in_places = [x.source for x in list(trans.in_arcs)]
        out_places = [x.target for x in list(trans.out_arcs)]
        for pl1 in in_places:
            for pl2 in out_places:
                graph.add_edge(pl1.name, pl2.name)
    return graph


def apply(bpmn_graph, parameters=None):
    """
    Converts a BPMN graph to an accepting Reset Inhibitor net

    Parameters
    --------------
    bpmn_graph
        BPMN graph
    parameters
        Parameters of the algorithm

    Returns
    --------------
    net
        Petri net
    im
        Initial marking
    fm
        Final marking
    """
    if parameters is None:
        parameters = {}

    import networkx as nx
    from pm4py.objects.bpmn.obj import BPMN

    include_events = parameters[INCLUDE_EVENTS] if INCLUDE_EVENTS in parameters else True

    # Preprocessing step that removes multiple arcs from a task/event object and replaces them with a XOR
    nodes = [node for node in bpmn_graph.get_nodes()]
    for node in nodes:
        if not isinstance(node, BPMN.Gateway):
            if len(node.get_in_arcs()) > 1:
                inputs = [flow.get_source() for flow in node.get_in_arcs()]
                flows = [flow for flow in node.get_in_arcs()]
                for flow in flows:
                    bpmn_graph.remove_flow(flow)
                exclusive_gateway = BPMN.ExclusiveGateway(id="XOR-input-" + node.get_id(), name="XOR-input-" + node.get_id(), 
                gateway_direction=BPMN.Gateway.Direction.CONVERGING, process=node.get_process())
                bpmn_graph.add_node(exclusive_gateway)
                for input_node in inputs:
                    flow =  BPMN.SequenceFlow(input_node, exclusive_gateway)
                    bpmn_graph.add_flow(flow)
                output_flow = BPMN.SequenceFlow(exclusive_gateway, node)
                bpmn_graph.add_flow(output_flow)
            if len(node.get_out_arcs()) > 1:
                outputs = [flow.get_target() for flow in node.get_out_arcs()]
                flows = [flow for flow in node.get_out_arcs()]
                for flow in flows:
                    bpmn_graph.remove_flow(flow)
                exclusive_gateway = BPMN.ExclusiveGateway(id="XOR-output-" + node.get_id(), name="XOR-output-" + node.get_id(), 
                gateway_direction=BPMN.Gateway.Direction.DIVERGING, process=node.get_process())
                bpmn_graph.add_node(exclusive_gateway)
                for output_node in outputs:
                    flow =  BPMN.SequenceFlow(exclusive_gateway, output_node)
                    bpmn_graph.add_flow(flow)
                input_flow = BPMN.SequenceFlow(node, exclusive_gateway)
                bpmn_graph.add_flow(input_flow)

    # IMPORTANT: In contrast to the RI method, we assign the process meta information only to those events that are named or are intermediate events

    # 1. initialize empty model
    net = PetriNet(bpmn_graph.get_name())
    im = Marking()
    fm = Marking()

    global_start_place = None
    global_end_place = None

    # 2. generate one place for each arc/flow
    flow_place = {}
    for flow in bpmn_graph.get_flows():
        # generate a place for each flow
        flow_id = str(flow.get_id())
        place = PetriNet.Place(flow_id + "@@@" + flow.get_process())
        net.places.add(place)
        flow_place[flow] = place

    # 3. loop through each node and transform them without composing them completely yet
    end_places = defaultdict(list)
    main_process_id = bpmn_graph.get_process_id()
    for node in bpmn_graph.get_nodes():
        node_id = str(node.get_id())
        if isinstance(node, BPMN.StartEvent):
            # name the start place according to the process it refers to
            start_place = PetriNet.Place("source@@@" + node_id + "@@@" + node.get_process())
            net.places.add(start_place)
            transition = PetriNet.Transition(name="t@@@" + node_id + "@@@" + node.get_process(), label=None)
            net.transitions.add(transition)
            out_arc_place = flow_place[node.get_out_arcs()[0]]
            # add arc from source to silent transition
            add_arc_from_to(start_place, transition, net)
            # add arc from silent transition to flow place
            add_arc_from_to(transition, out_arc_place, net)
            # if this is a global start event, add it to initial marking
            if node.get_process() == bpmn_graph.get_process_id():
                im[start_place] = 1
                global_start_place = start_place
        elif isinstance(node, (BPMN.IntermediateCatchEvent, BPMN.IntermediateThrowEvent, BPMN.Task)) or \
             (isinstance(node, BPMN.Gateway) and node.get_gateway_direction() == BPMN.Gateway.Direction.UNSPECIFIED and \
                 len(node.get_in_arcs()) == 1 and len(node.get_out_arcs()) == 1):
                if len(node.get_in_arcs()) > 0 and len(node.get_out_arcs()) > 0: # temporary fix
                    in_arc_place = flow_place[node.get_in_arcs()[0]]
                    # IMPORTANT: All intermediate events on global scope will be treated as termination events!
                    if isinstance(node, (BPMN.IntermediateCatchEvent, BPMN.IntermediateThrowEvent)):
                        label = None if node.get_process() == main_process_id else None if not include_events else (str(node.get_name()) if (node.get_name() != "" and not \
                            (isinstance(node, BPMN.Gateway) and node.get_gateway_direction() == BPMN.Gateway.Direction.UNSPECIFIED)) else None)
                        transition = PetriNet.Transition(name="t-intermediate@@@" + node.get_name() + "@@@" + node.get_id() + "@@@" + node.get_process(),
                            label=label, properties={"process": node.get_process(), "ignore": False})
                    else:
                        label = str(node.get_name()) if (node.get_name() != "" and not \
                            (isinstance(node, BPMN.Gateway) and node.get_gateway_direction() == BPMN.Gateway.Direction.UNSPECIFIED)) else None
                        transition = PetriNet.Transition(name="t@@@" + node_id + "@@@" + node.get_process(),
                            label=label, properties={"process": node.get_process()})
                    net.transitions.add(transition)
                    out_arc_place = flow_place[node.get_out_arcs()[0]]
                    # add arc from incoming flow place to (silent) transition
                    add_arc_from_to(in_arc_place, transition, net)
                    # add arc from (silent) transition to outgoing flow place
                    add_arc_from_to(transition, out_arc_place, net)
        elif isinstance(node, BPMN.EndEvent):
            prefix = "sink"
            suffix = node_id
            if isinstance(node, BPMN.ErrorEndEvent):
                prefix = "error"
                suffix = node.get_name() + "@@@" + node_id 
            elif isinstance(node, BPMN.CancelEndEvent):
                prefix = "cancel"
                suffix = node.get_name() + "@@@" + node_id 
            elif isinstance(node, BPMN.MessageEndEvent):
                prefix = "message"
                suffix = node.get_name() + "@@@" + node_id 
            elif isinstance(node, BPMN.TerminateEndEvent):
                prefix = "terminate"
                suffix = node.get_name() + "@@@" + node_id
            # name the end place according to the process it refers to
            end_place = PetriNet.Place(prefix + "@@@" + suffix + "@@@" + node.get_process())
            net.places.add(end_place)
            if node.get_process() == bpmn_graph.get_process_id() and isinstance(node, BPMN.NormalEndEvent):
                fm[end_place] = 1
                global_end_place = end_place
            end_places[node.get_process()].append(end_place)
            transition = PetriNet.Transition(name="t@@@" + node_id + "@@@" + node.get_process(), label=None)
            net.transitions.add(transition)
            in_arc_place = flow_place[node.get_in_arcs()[0]]
            # add arc from incoming flow place to silent transition
            add_arc_from_to(in_arc_place, transition, net)
            # add arc from silent transition to sink place
            add_arc_from_to(transition, end_place, net)
        elif isinstance(node, BPMN.ParallelGateway):
            if node.get_gateway_direction() == BPMN.Gateway.Direction.DIVERGING or len(node.get_out_arcs()) > 1:
                in_arc_place = flow_place[node.get_in_arcs()[0]]
                transition = PetriNet.Transition(name="t@@@" + node_id + "@@@" + node.get_process(), label=None)
                net.transitions.add(transition)
                out_arc_places = [flow_place[out_arc] for out_arc in node.get_out_arcs()]
                # add arc from incoming flow place to silent transition
                add_arc_from_to(in_arc_place, transition, net)
                # add arc from silent transition to all outgoing flow places
                for out_arc_place in out_arc_places:
                    add_arc_from_to(transition, out_arc_place, net)
            elif node.get_gateway_direction() == BPMN.Gateway.Direction.CONVERGING or len(node.get_in_arcs()) > 1:
                in_arc_places = [flow_place[in_arc] for in_arc in node.get_in_arcs()]
                transition = PetriNet.Transition(name="t@@@" + node_id + "@@@" + node.get_process(), label=None)
                net.transitions.add(transition)
                out_arc_place = flow_place[node.get_out_arcs()[0]]
                # add arc from all incoming flow places to silent transition
                for in_arc_place in in_arc_places:
                    add_arc_from_to(in_arc_place, transition, net)
                # add arc from silent transition to outgoing flow place
                add_arc_from_to(transition, out_arc_place, net)
        elif isinstance(node, BPMN.ExclusiveGateway):
            if node.get_gateway_direction() == BPMN.Gateway.Direction.DIVERGING or len(node.get_out_arcs()) > 1:
                in_arc_place = flow_place[node.get_in_arcs()[0]]
                out_arc_places = [flow_place[out_arc] for out_arc in node.get_out_arcs()]
                # add silent transition or each out arc (decision option)
                for i, out_arc_place in enumerate(out_arc_places):
                    transition = PetriNet.Transition(name="t@@@" + node_id + str(i) + "@@@" + node.get_process(), label=None)
                    net.transitions.add(transition)
                    # add arc from incoming flow place to silent transition
                    add_arc_from_to(in_arc_place, transition, net)
                    # add arc from silent transition to outgoing flow place
                    add_arc_from_to(transition, out_arc_place, net)
            elif node.get_gateway_direction() == BPMN.Gateway.Direction.CONVERGING or len(node.get_in_arcs()) > 1:
                in_arc_places = [flow_place[in_arc] for in_arc in node.get_in_arcs()]
                out_arc_place = flow_place[node.get_out_arcs()[0]]
                # add silent transition or each in arc (decision option)
                for i, in_arc_place in enumerate(in_arc_places):
                    transition = PetriNet.Transition(name="t@@@" + node_id + str(i) + "@@@" + node.get_process(), label=None)
                    net.transitions.add(transition)
                    # add arc from incoming flow place to silent transition
                    add_arc_from_to(in_arc_place, transition, net)
                    # add arc from silent transition to outgoing flow place
                    add_arc_from_to(transition, out_arc_place, net)


    # TODO: sort subprocesses by hierarchy?
    # 4. loop through each node again, this time we glue together subprocesses with the outside world by two silent transitions
    for subprocess in get_subprocesses_sorted_by_depth(bpmn_graph):
        activity_id = subprocess.get_id()
        # assuming one unique source of the subprocess, there needs to be a silent transition from the incoming flow place to the source place
        in_arc_place = flow_place[subprocess.get_in_arcs()[0]]
        source_place = get_place_by_prefix_postfix(net, "source", activity_id, "@@@")
        silent_start_transition = PetriNet.Transition(name="t-start-subprocess@@@" + activity_id + "@@@" + subprocess.get_process(),
            label=None)
        net.transitions.add(silent_start_transition)
            # add arc from incoming flow place to newly created silent transition
        add_arc_from_to(in_arc_place, silent_start_transition, net)
        # add arc from silent transition to source place of subprocess
        add_arc_from_to(silent_start_transition, source_place, net)
        # assuming block strucuturedness, there is a unique normal end event in the subprocess that needs to be connected to the outgoing flow of the subprocess
        out_arc_place = flow_place[subprocess.get_out_arcs()[0]]
        # get "normal" end event place of subprocess
        sink_place = get_place_by_prefix_postfix(net, "sink", activity_id, "@@@")
        silent_end_transition = PetriNet.Transition(name="t-end-subprocess@@@" + activity_id + "@@@" + subprocess.get_process(),
            label=None)
        net.transitions.add(silent_end_transition)
        # add arc from subprocess end place to newly created silent transition
        add_arc_from_to(sink_place, silent_end_transition, net)
        # add arc from silent transition to outgoing flow place
        add_arc_from_to(silent_end_transition, out_arc_place, net)

    # 5. loop through normal tasks and handle their boundary events
    for node in bpmn_graph.get_nodes():
        node_id = str(node.get_id())
        if isinstance(node, BPMN.Activity):
        #if isinstance(node, BPMN.BoundaryEvent):
            activity_id = node.get_id()
            boundary_events = get_boundary_events_of_activity(activity_id, bpmn_graph)
            if len(boundary_events) > 0:
                if isinstance(node, BPMN.Task):
                    in_arc_place = flow_place[node.get_in_arcs()[0]]
                    for boundary_event in boundary_events:
                        boundary_transition = PetriNet.Transition(name="t-boundary@@@" + str(boundary_event.get_id())  + "@@@" + node.get_process(), \
                            label=boundary_event.get_name() if boundary_event.get_name() != "" else None, properties={"process": node.get_process()})
                        net.transitions.add(boundary_transition)
                        out_arc_place = flow_place[boundary_event.get_out_arcs()[0]]
                        # add arc from incoming flow place to newly created (silent) transition
                        add_arc_from_to(in_arc_place, boundary_transition, net)
                        # add arc from newly created (silent) transition to outgoing flow place
                        add_arc_from_to(boundary_transition, out_arc_place, net)


    # 6. loop through subprocesses and handle their boundary events
    for subprocess in get_subprocesses_sorted_by_depth(bpmn_graph):
        activity_id = subprocess.get_id()
        boundary_events = get_boundary_events_of_activity(activity_id, bpmn_graph)
        # sink inside subprocess
        normal_sink_place = get_place_by_prefix_postfix(net, "sink", activity_id, "@@@")
        # termination events inside subprocess
        termination_events = get_termination_events_of_subprocess_for_pnet(activity_id, bpmn_graph)
        # entry point of subprocess
        subprocess_start_transition = get_transition_by_name(net, "t-start-subprocess@@@" + activity_id + "@@@" + subprocess.get_process())
        # end point of subprocess
        subprocess_end_transition = get_transition_by_name(net, "t-end-subprocess@@@" + activity_id + "@@@" + subprocess.get_process())

        # TODO: handle subrprocesses without any boundary, there are probably errors if we have a normal subprocess with a subprocess that has cancellation
        if not (len(boundary_events) > 0 or len(termination_events) > 0):
            for transition in net.transitions:
                if "process" in transition.properties and transition.properties["process"] == activity_id:
                    transition.properties["process"] = subprocess.get_process()
            for child_subprocess in get_all_direct_child_subprocesses(activity_id, bpmn_graph):
                child_subprocess.set_process(subprocess.get_process())
        else:
            # add OK and NOK flag places
            ok_place = PetriNet.Place("OK@@@" + "@@@" + activity_id)
            nok_place = PetriNet.Place("NOK@@@" + "@@@" + activity_id)
            net.places.add(ok_place)
            net.places.add(nok_place)
            # outcoming place of subprocess in case of normal flow
            subprocess_out_place = list(subprocess_end_transition.out_arcs)[0].target
            add_arc_from_to(subprocess_start_transition, ok_place, net)
            add_arc_from_to(ok_place, subprocess_end_transition, net)
            # add skip transitions for all transitions INSIDE the subprocess excluding the subprocesses inside the subprocess
            transitions_inside_subprocess = [transition for transition in net.transitions if "process" in transition.properties and transition.properties["process"] == activity_id \
                and (not transition.properties["ignore"] if "ignore" in transition.properties else True)]
            for i, transition in enumerate(transitions_inside_subprocess):
                skip_transition = PetriNet.Transition(name="t-skip@@@" + str(i) + "@@@" + activity_id, label=None)
                net.transitions.add(skip_transition)
                for arc in transition.in_arcs:
                    add_arc_from_to(arc.source, skip_transition, net)
                for arc in transition.out_arcs:
                    add_arc_from_to(skip_transition, arc.target, net)
                # transitions from and to ok/nok place
                add_arc_from_to(skip_transition, nok_place, net)
                add_arc_from_to(nok_place, skip_transition, net)
                # bidirectional arc from ok to transition
                add_arc_from_to(transition, ok_place, net)
                add_arc_from_to(ok_place, transition, net)

            # add ok connection to all direct or inderect children
            for child_subprocess in get_all_child_subprocesses(activity_id, bpmn_graph):
                child_subprocess_id = child_subprocess.get_id()
                transitions_inside_childprocess = [transition for transition in net.transitions if "process" in transition.properties and transition.properties["process"] == child_subprocess_id  \
                    and (not transition.properties["ignore"] if "ignore" in transition.properties else True)]
                for transition in transitions_inside_childprocess:
                    # bidirectional arc from ok to transition
                    add_arc_from_to(transition, ok_place, net)
                    add_arc_from_to(ok_place, transition, net)
            # add ok/nok connections to DIRECT child processes
            for child_subprocess in get_all_direct_child_subprocesses(activity_id, bpmn_graph):
                child_subprocess_id = child_subprocess.get_id()
                child_sink_place = get_place_by_prefix_postfix(net, "sink", child_subprocess_id, "@@@")
                child_end_transition = get_transition_by_name(net, "t-end-subprocess@@@" +  child_subprocess_id + "@@@" + activity_id)
                child_out_place = list(child_end_transition.out_arcs)[0].target
                
                cancel_transition = PetriNet.Transition(name="t-cancel@@@" + child_subprocess_id + "@@@" + activity_id, label=None)
                cancel_place = PetriNet.Place(name="cancel@@@" + child_subprocess_id + "@@@" + activity_id)
                cancel_nok_transition = PetriNet.Transition(name="t-cancel-nok@@@" + child_subprocess_id + "@@@" + activity_id, label=None)
                net.transitions.add(cancel_transition)
                net.places.add(cancel_place)
                net.transitions.add(cancel_nok_transition)
                # add connections
                child_ok_place = get_place_by_prefix_postfix(net, "OK", child_subprocess_id, "@@@")
                child_nok_place = get_place_by_prefix_postfix(net, "NOK", child_subprocess_id, "@@@")
                add_arc_from_to(child_ok_place, cancel_transition, net)
                add_arc_from_to(cancel_transition, child_nok_place, net)
                add_arc_from_to(cancel_transition, cancel_place, net)
                add_arc_from_to(cancel_transition, nok_place, net)
                add_arc_from_to(nok_place, cancel_transition, net)
                add_arc_from_to(child_nok_place, cancel_nok_transition, net)
                add_arc_from_to(cancel_place, cancel_nok_transition, net)
                add_arc_from_to(child_sink_place, cancel_nok_transition, net)
                add_arc_from_to(cancel_nok_transition, child_out_place, net)

            # handle the concrete boundary events
            for intermediate_event in boundary_events + termination_events:
                intermediate_event_name = intermediate_event.get_name()

                # handle termination event inside subprocess, for pnets it is the same as handling internal errors!
                if isinstance(intermediate_event, (BPMN.IntermediateThrowEvent, BPMN.IntermediateCatchEvent)):
                    termination_transition = get_transition_by_name(net, "t-intermediate@@@" + intermediate_event.get_name() + "@@@" + intermediate_event.get_id() + "@@@" + activity_id)
                    termination_transition.label = None
                    arcs = [arc for arc in termination_transition.out_arcs if arc.target == ok_place]
                    for arc in arcs:
                        remove_arc(net, arc)
                    add_arc_from_to(termination_transition, nok_place, net)

                    # add the final exit process transition
                    exit_process_transition = PetriNet.Transition(name="t-exit@@@" + intermediate_event.get_id() + "@@@" + activity_id, label=None)
                    termination_place = PetriNet.Place(name="termination-place@@@" + intermediate_event.get_id() + "@@@" + activity_id)
                    net.transitions.add(exit_process_transition)
                    net.places.add(termination_place)
                    add_arc_from_to(nok_place, exit_process_transition, net)
                    add_arc_from_to(normal_sink_place, exit_process_transition, net)
                    add_arc_from_to(termination_transition, termination_place, net)
                    add_arc_from_to(termination_place, exit_process_transition, net)
                    add_arc_from_to(exit_process_transition, subprocess_out_place, net)

                else:
                    # the place that connects the boundary event to the side branch
                    out_arc_place = flow_place[intermediate_event.get_out_arcs()[0]]
                    # add exception place that all transitions corresponding to the same boundary event share
                    exception_place = PetriNet.Place("exception@@@" + intermediate_event.get_name() + "@@@" + activity_id)
                    net.places.add(exception_place)

                    # internal exception
                    if isinstance(intermediate_event, (BPMN.ErrorBoundaryEvent, BPMN.CancelBoundaryEvent)):
                        # exception transitions that correspond to the same boundary event, they already exist as intermediate events
                        exception_transitions = [transition for transition in net.transitions if len(transition.name.split("@@@")) > 0 and \
                            transition.name.split("@@@")[0] == "t-intermediate" and transition.name.split("@@@")[1] == intermediate_event.get_name() and \
                            transition.name.split("@@@")[-1] == activity_id]
                        for exception_transition in exception_transitions:
                            in_place = [arc.source for arc in exception_transition.in_arcs if arc.source != ok_place][0]
                            real_exception_transitions = [arc.source for arc in in_place.in_arcs if "skip" not in arc.source.name and arc.source.label != None]
                            if len(real_exception_transitions) > 0:
                                real_exception_transition = real_exception_transitions[0]
                                # bidirectional arc from ok to transition only in one direction, hence we have to remove the other direction
                                arcs = [arc for arc in real_exception_transition.out_arcs if arc.target == ok_place]
                                for arc in arcs:
                                    remove_arc(net, arc)
                                add_arc_from_to(real_exception_transition, nok_place, net)
                                add_arc_from_to(real_exception_transition, exception_place, net)
                                # remove arcs from exception transition to ok place
                                arcs_to_remove = [arc for arc in exception_transition.out_arcs if arc.target == ok_place] + [arc for arc in exception_transition.in_arcs if arc.source == ok_place]
                                for arc in arcs_to_remove:
                                    remove_arc(net, arc)
                                real_skip_transition = [arc.source for arc in in_place.in_arcs if "skip" in arc.source.name][0]
                                real_skip_transition_arcs = [arc for arc in in_place.in_arcs if "skip" in arc.source.name]
                                for arc in real_skip_transition_arcs:
                                    remove_arc(net, arc)
                                out_place = [arc.target for arc in exception_transition.out_arcs][0]
                                add_arc_from_to(real_skip_transition, out_place, net)
                                exception_skip = [arc.target for arc in in_place.out_arcs if "skip" in arc.target.name][0]
                                remove_transition(net, exception_skip)
                            else:
                                arcs = [arc for arc in exception_transition.out_arcs if arc.target == ok_place]
                                for arc in arcs:
                                    remove_arc(net, arc)
                                add_arc_from_to(exception_transition, nok_place, net)
                                add_arc_from_to(exception_transition, exception_place, net)

                    # external exception
                    else:
                        # exception transitions that correspond to the same boundary event, they must be created yet
                        boundary_transition_label = None if (not include_events) or (intermediate_event_name == "") else intermediate_event_name
                        exception_transition = PetriNet.Transition(name="t-intermediate@@@" + intermediate_event_name + "@@@" + activity_id,
                            label=boundary_transition_label) 
                        net.transitions.add(exception_transition)
                        add_arc_from_to(ok_place, exception_transition, net)
                        add_arc_from_to(exception_transition, nok_place, net)
                        add_arc_from_to(exception_transition, exception_place, net)

                    # add the final exit subprocess transition
                    exit_subprocess_transition = PetriNet.Transition(name="t-exit@@@" + intermediate_event_name + "@@@" + activity_id,
                        label=None)
                    net.transitions.add(exit_subprocess_transition)
                    add_arc_from_to(exception_place, exit_subprocess_transition, net)
                    add_arc_from_to(nok_place, exit_subprocess_transition, net)
                    add_arc_from_to(normal_sink_place, exit_subprocess_transition, net)
                    add_arc_from_to(exit_subprocess_transition, out_arc_place, net)
                

    # handle termination end events globally
    termination_transitions = [transition for transition in net.transitions if len(transition.name.split("@@@")) > 0 and transition.name.split("@@@")[-1] == main_process_id and \
        transition.name.split("@@@")[0] == "t-intermediate"]
    if len(termination_transitions) > 0:
        # add OK and NOK flag places globally
        ok_place = PetriNet.Place("OK@@@" + "@@@" + main_process_id)
        nok_place = PetriNet.Place("NOK@@@" + "@@@" + main_process_id)
        net.places.add(ok_place)
        net.places.add(nok_place)
        global_start_transition = list(global_start_place.out_arcs)[0].target
        global_end_transition = list(global_end_place.in_arcs)[0].source
        pre_sink_place = list(global_end_transition.in_arcs)[0].source
        add_arc_from_to(global_start_transition, ok_place, net)
        add_arc_from_to(ok_place, global_end_transition, net)
        # add skip transitions for all transitions INSIDE the process
        transitions_inside_process = [transition for transition in net.transitions if transition != global_start_transition and transition != global_end_transition and \
            "process" in transition.properties and transition.properties["process"] == main_process_id]
        for i, transition in enumerate(transitions_inside_process):
            skip_transition = PetriNet.Transition(name="t-skip@@@" + str(i) + "@@@" + main_process_id, label=None)
            net.transitions.add(skip_transition)
            for arc in transition.in_arcs:
                add_arc_from_to(arc.source, skip_transition, net)
            for arc in transition.out_arcs:
                add_arc_from_to(skip_transition, arc.target, net)
            # transitions from and to ok/nok place
            add_arc_from_to(skip_transition, nok_place, net)
            add_arc_from_to(nok_place, skip_transition, net)
            add_arc_from_to(transition, ok_place, net)
            add_arc_from_to(ok_place, transition, net)

        # add ok connection to all direct or inderect children
        for child_subprocess in get_all_child_subprocesses(main_process_id, bpmn_graph):
            child_subprocess_id = child_subprocess.get_id()
            transitions_inside_childprocess = [transition for transition in net.transitions if "process" in transition.properties and transition.properties["process"] == child_subprocess_id]
            for transition in transitions_inside_childprocess:
                # bidirectional arc from ok to transition
                add_arc_from_to(transition, ok_place, net)
                add_arc_from_to(ok_place, transition, net)
        # add ok/nok connections to DIRECT child processes
        for child_subprocess in get_all_direct_child_subprocesses(main_process_id, bpmn_graph):
            child_subprocess_id = child_subprocess.get_id()
            child_sink_place = get_place_by_prefix_postfix(net, "sink", child_subprocess_id, "@@@")
            child_end_transition = get_transition_by_name(net, "t-end-subprocess@@@" +  child_subprocess_id + "@@@" + main_process_id)
            child_out_place = list(child_end_transition.out_arcs)[0].target
            
            cancel_transition = PetriNet.Transition(name="t-cancel@@@" + child_subprocess_id + "@@@" + main_process_id, label=None)
            cancel_place = PetriNet.Place(name="cancel@@@" + child_subprocess_id + "@@@" + main_process_id)
            cancel_nok_transition = PetriNet.Transition(name="t-cancel-nok@@@" + child_subprocess_id + "@@@" + main_process_id, label=None)
            net.transitions.add(cancel_transition)
            net.places.add(cancel_place)
            net.transitions.add(cancel_nok_transition)
            # add connections
            child_ok_place = get_place_by_prefix_postfix(net, "OK", child_subprocess_id, "@@@")
            child_nok_place = get_place_by_prefix_postfix(net, "NOK", child_subprocess_id, "@@@")
            add_arc_from_to(child_ok_place, cancel_transition, net)
            add_arc_from_to(cancel_transition, child_nok_place, net)
            add_arc_from_to(cancel_transition, cancel_place, net)
            add_arc_from_to(cancel_transition, nok_place, net)
            add_arc_from_to(nok_place, cancel_transition, net)
            add_arc_from_to(child_nok_place, cancel_nok_transition, net)
            add_arc_from_to(cancel_place, cancel_nok_transition, net)
            add_arc_from_to(child_sink_place, cancel_nok_transition, net)
            add_arc_from_to(cancel_nok_transition, child_out_place, net)

        # handle the concrete termination events
        for termination_transition in termination_transitions:
            termination_transition.label = None
            arcs = [arc for arc in termination_transition.out_arcs if arc.target == ok_place]
            for arc in arcs:
                remove_arc(net, arc)
            add_arc_from_to(termination_transition, nok_place, net)

        # add the final exit process transition
        exit_process_transition = PetriNet.Transition(name="t-exit@@@" + "@@@" + main_process_id, label=None)
        net.transitions.add(exit_process_transition)
        add_arc_from_to(nok_place, exit_process_transition, net)
        add_arc_from_to(pre_sink_place, exit_process_transition, net)
        add_arc_from_to(exit_process_transition, global_end_place, net)
    
    reduction.apply_simple_reduction(net)

    return net, im, fm
