function get_human_subs(human_name, to){
    $.ajax({
        type:"post",
            url:"/humans/"+human_name+"/config",
            success:function(data){
                if (data.ok == true) {
                    result = "";
                    data.data.subs.forEach(function(sub){
                        result += sub+" ";
                    });
                    to.text(result);
                }
            }
    });
};

$("#human-name option").on('click', function(e){
         var human_name = $(e.target).attr("id");
         if (human_name != undefined){
            get_human_subs(human_name, $("#human-subs"));
         }

});

function show_human_live_state(){
        var name = $("#human-name").text();
        console.log("will send... to "+name);
        $.ajax({
            type:"POST",
            url:"/humans/"+name+"/state",
            success:function(x){
                 console.log(x);
                 if (x.human == name){
                    $("#human-live-state").text(x.state.human_state+" [process work: "+x.state.process_state.work+"]");
                 }
            }
        })
    }

setInterval(function() {
    show_human_live_state()
}, 1000);