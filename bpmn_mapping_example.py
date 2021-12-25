import os
from pm4py.objects.bpmn.importer import importer as bpmn_importer
from pm4py.objects.conversion.bpmn import converter as reset_net_converter
from pm4py.visualization.petri_net import visualizer as pn_visualizer

#bpmn_graph = bpmn_importer.apply(os.path.join("tests","input_data","running-example.bpmn"))
#bpmn_graph = bpmn_importer.apply(os.path.join("tests","input_data","cancellation.bpmn"))
bpmn_graph = bpmn_importer.apply(os.path.join("bpmn-graphs", "Experiment3.bpmn"))

parameters = {}
# should the amount of reset arcs be minimized wherever possible?
parameters["optimize"] = True
# should boundary events be treated as labelled activities?
parameters['include_events'] = True
reset_net, initial_marking, final_marking = reset_net_converter.apply(bpmn_graph, variant=reset_net_converter.RESET_VARIANT, parameters=parameters)

gviz = pn_visualizer.apply(reset_net, initial_marking, final_marking)
pn_visualizer.view(gviz)